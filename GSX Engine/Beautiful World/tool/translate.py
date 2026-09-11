#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate.py — batch JP→ZH machine translation for the GSX engine extract.

Relay  : https://pro.gemai.cc  (OpenAI-compatible /v1/chat/completions)
Model  : deepseek-v4-flash

Notes discovered while calibrating this relay (see docs/engine_report.md):
  * `thinking={"type":"disabled"}` is accepted but NOT honoured.  The model
    starts producing very long hidden reasoning once the request holds more
    than ~35 dialogue lines, spending the whole max_tokens on reasoning_tokens
    and returning an EMPTY content (finish_reason="length").
  * Therefore the batch size is kept small (default 24) and every failure is
    recovered by recursively halving the batch.

Features: dedup + on-disk cache (resumable), tolerant JSON parsing, splitting
on mismatch, deterministic punctuation repair, hanzi2kanji post-mapping and
cp932 validation.
"""
import os, sys, json, re, time, random, argparse
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

API_URL = 'https://pro.gemai.cc/v1/chat/completions'
API_KEY = 'sk-OUY9YqTKlgjf4G4pplkWRxPxeSjRUC9RNEBYip1s5MbgXpEK'
MODEL = 'deepseek-v4-flash'

SYS_BASE = ('你是日译中翻译。把JSON数组里的日文逐条翻译成简体中文，输出等长JSON数组，'
            '只输出JSON、不要解释、不要编号、不要markdown。规则：'
            '1) 保留 [[BR]] 和 [[SP]] 标记原样；'
            '2) 台词保留「」引号；'
            '3) 译文必须是简体中文，绝对不要出现繁体字，也不要残留日文假名；'
            '4) 人名、昵称、店名等专有名词必须译成中文音译/意译，全篇保持一致；'
            '5) 尽量使用日语中存在的汉字写法，避免生僻字。')

# proper nouns that must be rendered in Chinese, never left in kana
NAMES = [
    ('らいん', '莱茵'),
    ('セカンド', '赛肯德'),
    ('ひいちゃん', '小希'),
    ('ひぃちゃん', '小希'),
    ('ぴよちゃん', '小阳'),
    ('オーキ先輩', '奥基前辈'),
    ('オーキ', '奥基'),
    ('カンナ', '佳奈'),
    ('マイマイ', '舞舞'),
    ('ミカリンゴ', '美香苹果'),
    ('ハル', '小春'),
    ('ナル', '小鳴'),
    ('ときのちゃん', '小鴇乃'),
    ('ときのん', '小鴇乃'),
    ('キサラギ', '如月'),
    ('ユユ', '小柚'),
    ('ツグハル', '告春'),
    ('くるくる', '团团转'),
    ('リューさん', '龙前辈'),
    ('リュー先生', '龙老师'),
    ('リュー', '龙'),
    ('グレイスロープ', '格雷斯坡'),
    ('ソックス', '短袜'),
    ('さまよえる', '彷徨'),
    ('フライング', '飞行'),
]

GLOSSARY = {
    'ホメオスタシスリンク': '恆常性連結',
    'プロジェクト': '計畫',
    'ペーパーバッグ': '紙袋',
    'サッカー部': '足球部',
}

SYSTEM = SYS_BASE + '人名固定译法：' + '、'.join('%s→%s' % n for n in NAMES) + '。'


# ------------------------------------------------------------------ http
def call_api(messages, retries=6):
    # NOTE: on this relay only `reasoning_effort='none'` actually disables the
    # hidden reasoning of deepseek-v4-flash.  `thinking={'type':'disabled'}` is
    # accepted but ignored; without `reasoning_effort` the model burns the whole
    # max_tokens on reasoning_tokens for batches larger than ~35 lines and
    # returns an EMPTY content (finish_reason='length').
    payload = {'model': MODEL, 'messages': messages, 'temperature': 0.5,
               'max_tokens': 8192, 'thinking': {'type': 'disabled'},
               'reasoning_effort': 'none'}
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(
                API_URL, data=body,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + API_KEY})
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
            ch = d['choices'][0]
            return ch['message'].get('content') or '', ch
        except Exception as e:
            last = e
            time.sleep(1.5 * (a + 1) + random.random())
    raise RuntimeError('api failed: %r' % last)


# ------------------------------------------------------------------ parsing
def parse_array(content, n):
    if not content:
        return None
    m = re.search(r'\[.*\]', content, re.S)
    if not m:
        return None
    txt = m.group(0)
    try:
        arr = json.loads(txt)
        if isinstance(arr, list) and len(arr) == n:
            return [str(x) for x in arr]
    except Exception:
        pass
    # tolerant: every double-quoted string inside the [ ] range
    out, i, L = [], 1, len(txt)
    while i < L - 1 and len(out) < n:
        if txt[i] == '"':
            j = i + 1
            buf = []
            while j < L and txt[j] != '"':
                if txt[j] == '\\' and j + 1 < L:
                    esc = txt[j + 1]
                    buf.append({'n': '\n', 't': '\t', '"': '"', '\\': '\\',
                                '/': '/'}.get(esc, esc))
                    j += 2
                    continue
                buf.append(txt[j])
                j += 1
            out.append(''.join(buf))
            i = j + 1
        else:
            i += 1
    if len(out) == n:
        return out
    return None


# ------------------------------------------------------------------ post-process
class HanziMap:
    """hanzi2kanji_table.txt semantics
    ---------------------------------
    Each line `K<TAB>V` means **"to have the game render K, write V"**:

        the glyph the player sees for code point V is the one belonging to K

    (97 % of the keys are simplified-Chinese characters that cannot be encoded
    in cp932 at all, so writing the key directly is impossible — V is the slot
    the custom JIS font has been given the Chinese glyph for).

    Consequences, and why this class does two passes:

    * the model's raw output is Simplified Chinese, so most keys fire directly;
    * but the model happily emits **Japanese/traditional** variants (來 會 個 點
      說 櫻 ...).  Several of those are themselves used as *slots* by the table
      (礌->來, 苁->會, 鲟->個, 爸->點, 伥->櫻 ...), i.e. writing them makes the
      game show the *key* — a completely unrelated glyph.  So every traditional
      character is first folded to its simplified form (trad2simp.txt) and only
      then looked up in the table.
    """

    def __init__(self, path, supp=None):
        self.m = {}
        for line in open(path, encoding='utf-8'):
            line = line.rstrip('\r\n')
            if not line or line.startswith('#') or line.startswith(';'):
                continue
            p = line.split('\t')
            if len(p) >= 2 and p[0]:
                self.m[p[0]] = p[1][0] if p[1] else p[0]
        # supplementary mapping (applied afterwards, and never used to overwrite
        # a mapping given by hanzi2kanji_table.txt)
        if supp and os.path.exists(supp):
            for line in open(supp, encoding='utf-8'):
                line = line.rstrip('\r\n')
                if not line or line.startswith('#') or line.startswith(';'):
                    continue
                p = line.split('\t')
                if len(p) >= 2 and p[0] and p[1]:
                    self.m.setdefault(p[0], p[1][0])
        for k, v in GLOSSARY.items():
            self.m.setdefault(k, v)
        # what the player actually sees for a written character
        self.shown = {v: k for k, v in self.m.items() if v}
        # traditional -> simplified
        # trad2simp.txt is generated; t2s_extra.txt is hand written and wins
        self.t2s = {}
        for fn, override in (('trad2simp.txt', False), ('t2s_extra.txt', True)):
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), fn)
            if not os.path.exists(fp):
                continue
            for line in open(fp, encoding='utf-8'):
                line = line.rstrip('\r\n')
                if not line or line.startswith('#') or line.startswith(';'):
                    continue
                p = line.split('\t')
                if len(p) >= 2 and p[0] and p[1]:
                    if override:
                        self.t2s[p[0]] = p[1][0]
                    else:
                        self.t2s.setdefault(p[0], p[1][0])
        # simplified -> traditional (fallback when the simplified char is not
        # representable in cp932 at all, e.g. 鸫 -> 鴇)
        self.rev = {}
        for a, b in self.t2s.items():
            self.rev.setdefault(b, []).append(a)

    @staticmethod
    def encodable(c):
        try:
            c.encode('cp932')
            return True
        except UnicodeEncodeError:
            return False

    def shown_of(self, c):
        """The character the game renders when `c` is written."""
        return self.shown.get(c, c)

    def _one(self, c):
        s = self.t2s.get(c)
        if s is None:
            w = self.m.get(c)
            out = w if w is not None else c
        else:
            # `c` is a traditional/Japanese glyph; `s` is what it should look like
            if s in self.m:
                out = self.m[s]
            elif self.encodable(s) and self.shown_of(s) == s:
                out = s
            elif self.encodable(c) and self.shown_of(c) == s:
                # writing `c` already renders `s` (e.g. 鴇 renders 鸨)
                out = c
            elif self.encodable(s):
                out = s
            elif self.encodable(c):
                out = c
            else:
                out = s
        if not self.encodable(out):
            # no cp932 code point at all: try a traditional variant that has one
            for cand in self.rev.get(out, ()):
                if self.encodable(cand):
                    return cand
        return out

    def apply(self, s):
        return ''.join(self._one(c) for c in s)


BANNED_REPL = {
    '\u266a': '', '\u266b': '', '\u266c': '', '\u2190': '\u2190', '\u2192': '\u2192',
    '\u2713': '', '\u2606': '\u2606', '\u2605': '\u2605',
}


def cp932_clean(s):
    """Return (clean_string, dropped_chars)."""
    out, dropped = [], []
    for c in s:
        try:
            c.encode('cp932')
            out.append(c)
        except UnicodeEncodeError:
            rep = BANNED_REPL.get(c)
            if rep:
                out.append(rep)
            else:
                dropped.append(c)
    return ''.join(out), dropped


def fix_punct(s):
    """No lone punctuation directly after a line-break marker."""
    for mark in ('\u3002', '\uff0c', '\u3001', '\uff01', '\uff1f', '\u2026',
                 '\u300d', '\u300f', '\u2019', '\u201d'):
        s = s.replace('[[BR]]' + mark, mark + '[[BR]]')
        s = s.replace('[[SP]]' + mark, mark + '[[SP]]')
    return s


def normalise_markers(s):
    s = re.sub(r'\[\[\s*(br|BR|Br)\s*\]\]', '[[BR]]', s)
    s = re.sub(r'\[\[\s*(sp|SP|Sp)\s*\]\]', '[[SP]]', s)
    s = s.replace('\u3010BR\u3011', '[[BR]]').replace('\u3010SP\u3011', '[[SP]]')
    return s


# ------------------------------------------------------------------ worker
def translate_batch(lines, depth=0, system=None):
    """Return list of translations or None."""
    if not lines:
        return []
    msgs = [{'role': 'system', 'content': system or SYSTEM},
            {'role': 'user', 'content': json.dumps(lines, ensure_ascii=False)}]
    try:
        content, ch = call_api(msgs)
    except Exception as e:
        content, ch = '', None
        print('  [api-error] %r' % e)
    n = len(lines)
    res = parse_array(content, n)
    if res is not None:
        return res
    if n == 1:
        return None
    half = n // 2
    print('  [split] batch %d -> %d + %d' % (n, half, n - half), flush=True)
    a = translate_batch(lines[:half], depth + 1, system)
    b = translate_batch(lines[half:], depth + 1, system)
    if a is None or b is None:
        return None
    return a + b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src_json')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--batch', type=int, default=24)
    ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()

    entries = json.load(open(a.src_json, encoding='utf-8'))
    hm = HanziMap(os.path.join(ROOT, 'hanzi2kanji_table.txt'))
    cache = {}
    if os.path.exists(a.cache):
        cache = json.load(open(a.cache, encoding='utf-8'))
    print('[translate] %d entries, %d cached' % (len(entries), len(cache)))

    uniq = []
    seen = set()
    for e in entries:
        s = e['pre_jp']
        if s in cache or s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    print('[translate] %d unique strings to translate' % len(uniq))

    batches = [uniq[i:i + a.batch] for i in range(0, len(uniq), a.batch)]
    done = [0]
    lock = __import__('threading').Lock()

    def work(b):
        r = translate_batch(b)
        with lock:
            if r is None:
                for s in b:
                    cache.setdefault('__FAIL__', [])
                print('[fail] batch of %d' % len(b))
                return
            for s, t in zip(b, r):
                cache[s] = t
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == len(batches):
                json.dump(cache, open(a.cache, 'w', encoding='utf-8'), ensure_ascii=False)
                print('[translate] %d/%d batches' % (done[0], len(batches)))

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, batches))
    cache.pop('__FAIL__', None)
    json.dump(cache, open(a.cache, 'w', encoding='utf-8'), ensure_ascii=False)

    # ---- retry whatever the batched pass could not resolve --------------
    missing = [s for s in uniq if not cache.get(s)]
    if missing:
        print('[translate] retrying %d unresolved strings one by one' % len(missing),
              flush=True)
        retry_batches = [missing[i:i + 8] for i in range(0, len(missing), 8)]
        got = [0]

        def retry(b):
            r = translate_batch(b, system=None)
            if r:
                with lock:
                    for s, t in zip(b, r):
                        if t:
                            cache[s] = t
                    got[0] += 1
                    if got[0] % 5 == 0:
                        json.dump(cache, open(a.cache, 'w', encoding='utf-8'),
                                  ensure_ascii=False)

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(retry, retry_batches))
        json.dump(cache, open(a.cache, 'w', encoding='utf-8'), ensure_ascii=False)
        left = [s for s in uniq if not cache.get(s)]
        print('[translate] still unresolved: %d' % len(left), flush=True)

    dropped_all = {}
    nok = 0
    for e in entries:
        t = cache.get(e['pre_jp'])
        if not t:
            e['message'] = e['pre_jp']            # keep the original
            continue
        t = normalise_markers(t)
        t = hm.apply(t)
        t = fix_punct(t)
        t, dropped = cp932_clean(t)
        for c in dropped:
            dropped_all[c] = dropped_all.get(c, 0) + 1
        e['message'] = t
        nok += 1
    json.dump(entries, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[translate] %d/%d translated -> %s' % (nok, len(entries), a.out))
    if dropped_all:
        print('[translate] dropped non-cp932 chars:', dropped_all)


if __name__ == '__main__':
    main()
