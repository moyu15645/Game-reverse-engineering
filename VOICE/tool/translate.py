"""LOVELESS 批量 AI 翻译（中转站 pro.gemai.cc / deepseek-v4-flash，关闭推理）

用法:
  python tool/translate.py run   [--batch 200] [--workers 4] [--limit N]
  python tool/translate.py apply            # 把缓存写入 trans/<file>.json
  python tool/translate.py status

设计:
  · 全部条目展平成 (key = "<FILE>:<id>", jp) 任务列表
  · 分批（默认 200 条/批）请求，system 里带术语表
  · 结果逐行追加到 trans/_cache.jsonl，可随时中断续跑
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request

PROJ = r'E:\potential\LOVELESS終わらない物語\LOVELESS'
TEXT = os.path.join(PROJ, 'text')
TRANS = os.path.join(PROJ, 'trans')
CACHE = os.path.join(TRANS, '_cache.jsonl')
GLOSSARY = os.path.join(PROJ, 'tool', 'glossary.txt')

URL = 'https://pro.gemai.cc/v1/chat/completions'
KEY = 'sk-OUY9YqTKlgjf4G4pplkWRxPxeSjRUC9RNEBYip1s5MbgXpEK'
MODEL = 'deepseek-v4-flash'

SYS = ('你是资深日文→简体中文 galgame 本地化译者。'
       '把用户给出的每一行日文翻译成自然、口语化的简体中文。'
       '严格遵守术语表。保持「」『』等括号与 …… ── 等符号。'
       '只输出 "序号|译文" 格式，每行一条，行数必须与输入完全一致，不要任何解释。')


def load_glossary():
    if not os.path.exists(GLOSSARY):
        return ''
    lines = []
    for l in open(GLOSSARY, encoding='utf-8'):
        l = l.strip()
        if l and not l.startswith('#'):
            lines.append(l)
    return '\n'.join(lines)


def load_tasks():
    tasks = []
    for f in sorted(os.listdir(TEXT)):
        if not f.endswith('.json') or f.endswith('.meta.json'):
            continue
        base = f[:-5]
        ent = json.load(open(os.path.join(TEXT, f), encoding='utf-8'))
        for e in ent:
            tasks.append(('%s:%d' % (base, e['id']), e['pre_jp']))
    return tasks


def load_cache():
    done = {}
    if os.path.exists(CACHE):
        for l in open(CACHE, encoding='utf-8'):
            l = l.strip()
            if not l:
                continue
            try:
                r = json.loads(l)
                done[r['k']] = r['t']
            except Exception:
                pass
    return done


_lock = threading.Lock()
_fh = None


def post(body, retries=4):
    data = json.dumps(body).encode('utf-8')
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(
                URL, data=data,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + KEY})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
            time.sleep(3 * (a + 1))
    raise last


LINE = re.compile(r'^\s*(\d+)\s*[|｜:：]\s*(.*)$')


def do_batch(items, glossary):
    """items: list of (key, jp) -> dict key->translation"""
    body_text = '\n'.join('%d|%s' % (i + 1, jp.replace('\n', ' '))
                          for i, (_, jp) in enumerate(items))
    user = ''
    if glossary:
        user += '术语表：\n' + glossary + '\n\n'
    user += '请翻译下列各行：\n' + body_text
    out = post({'model': MODEL,
                'messages': [{'role': 'system', 'content': SYS},
                             {'role': 'user', 'content': user}],
                'temperature': 0.5,
                'thinking': {'type': 'disabled'},
                'max_tokens': 8192})
    txt = out['choices'][0]['message']['content']
    got = {}
    for l in txt.splitlines():
        m = LINE.match(l)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= len(items):
            got[items[n - 1][0]] = m.group(2).strip()
    return got


def cmd_run(args):
    os.makedirs(TRANS, exist_ok=True)
    global _fh
    tasks = load_tasks()
    done = load_cache()
    todo = [(k, jp) for k, jp in tasks if k not in done]
    if args.limit:
        todo = todo[:args.limit]
    print('tasks=%d cached=%d todo=%d' % (len(tasks), len(done), len(todo)))
    _fh = open(CACHE, 'a', encoding='utf-8')
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    glossary = load_glossary()
    print('batches=%d batch=%d workers=%d' % (len(batches), args.batch, args.workers))
    stat = {'ok': 0, 'miss': 0, 'err': 0}
    idx = [0]
    t0 = time.time()

    def worker(wid):
        while True:
            with _lock:
                if idx[0] >= len(batches):
                    return
                b = batches[idx[0]]
                idx[0] += 1
                bi = idx[0]
            try:
                got = do_batch(b, glossary)
            except Exception as e:
                stat['err'] += 1
                print('[w%d] batch %d ERR %s' % (wid, bi, e))
                continue
            miss = [k for k, _ in b if k not in got]
            stat['ok'] += len(got)
            stat['miss'] += len(miss)
            with _lock:
                for k, v in got.items():
                    _fh.write(json.dumps({'k': k, 't': v}, ensure_ascii=False) + '\n')
                _fh.flush()
            if bi % 5 == 0 or miss:
                el = time.time() - t0
                print('[w%d] batch %d/%d ok=%d miss=%d err=%d  %.0fs'
                      % (wid, bi, len(batches), stat['ok'], stat['miss'], stat['err'], el))

    ths = [threading.Thread(target=worker, args=(i,), daemon=True)
           for i in range(args.workers)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    _fh.close()
    print('DONE ok=%d miss=%d err=%d  %.0fs' % (stat['ok'], stat['miss'], stat['err'],
                                                time.time() - t0))


def cmd_status(args):
    tasks = load_tasks()
    done = load_cache()
    print('tasks=%d cached=%d remaining=%d' % (len(tasks), len(done),
                                               len(tasks) - len(done)))


def cmd_apply(args):
    """把缓存里的译文写回 trans/<file>.json（message 字段）。"""
    os.makedirs(TRANS, exist_ok=True)
    done = load_cache()
    miss = 0
    for f in sorted(os.listdir(TEXT)):
        if not f.endswith('.json') or f.endswith('.meta.json'):
            continue
        base = f[:-5]
        ent = json.load(open(os.path.join(TEXT, f), encoding='utf-8'))
        for e in ent:
            k = '%s:%d' % (base, e['id'])
            if k in done and done[k]:
                e['message'] = done[k]
            else:
                miss += 1
        json.dump(ent, open(os.path.join(TRANS, f), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        meta_src = os.path.join(TEXT, base + '.json.meta.json')
        meta = json.load(open(meta_src, encoding='utf-8'))
        json.dump(meta, open(os.path.join(TRANS, base + '.json.meta.json'), 'w',
                             encoding='utf-8'), ensure_ascii=False, indent=1)
    print('applied to trans/ ; untranslated entries: %d' % miss)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'status', 'apply'])
    ap.add_argument('--batch', type=int, default=200)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    {'run': cmd_run, 'status': cmd_status, 'apply': cmd_apply}[a.cmd](a)
