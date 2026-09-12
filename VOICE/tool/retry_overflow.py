"""对溢出条目做带长度约束的重译。

读 build/unpack_cn/_overflow.json，逐条请求「不超过 N 个全角字符」的压缩译文，
写回 trans/<file>.json（并追加到缓存）。
"""
import json
import os
import sys
import threading
import time

PROJ = r'E:\potential\LOVELESS終わらない物語\LOVELESS'
sys.path.insert(0, os.path.join(PROJ, 'tool'))
import hanzi_map
from translate import post, MODEL

OVF = os.path.join(PROJ, 'build', 'unpack_cn', '_overflow.json')
TRANS = os.path.join(PROJ, 'trans')
TABLE = hanzi_map.load()

SYS = ('你是资深日文→简体中文 galgame 本地化译者，擅长在严格字数限制下压缩表达。'
       '译文必须自然、保留原意与语气，且全角字符总数（含标点）不得超过给定上限。'
       '只输出译文本身，不要序号、不要引号、不要任何解释。')


def budget(msg, cap):
    """bytes of the mapped translation"""
    return len(hanzi_map.to_cp932(hanzi_map.convert(msg, TABLE))[0])


def retry(entries, out):
    """entries: list of (file, id, cap_bytes, jp, old_zh)"""
    lock = threading.Lock()
    idx = [0]
    done = {}

    def worker(wid):
        while True:
            with lock:
                if idx[0] >= len(entries):
                    return
                e = entries[idx[0]]
                idx[0] += 1
            f, i, cap, jp, old = e
            limit = cap // 2
            user = ('上限：%d 个全角字符（含标点）。\n日文：%s\n'
                    '（参考：上一版译文「%s」超长，请更精简）\n只输出译文：' % (limit, jp, old))
            for attempt in range(3):
                try:
                    r = post({'model': MODEL,
                              'messages': [{'role': 'system', 'content': SYS},
                                           {'role': 'user', 'content': user}],
                              'temperature': 0.4,
                              'thinking': {'type': 'disabled'},
                              'max_tokens': 256})
                    t = r['choices'][0]['message']['content'].strip().strip('"').strip()
                    if '\n' in t:
                        t = t.splitlines()[0].strip()
                    if t and budget(t, cap) <= cap:
                        with lock:
                            done[(f, i)] = t
                        break
                    user = ('上限：%d 个全角字符（含标点），必须更短。\n日文：%s\n只输出译文：'
                            % (max(2, limit - attempt - 1), jp))
                except Exception as ex:
                    time.sleep(2)
                    if attempt == 2:
                        print('ERR', f, i, ex, flush=True)
            with lock:
                if idx[0] % 25 == 0:
                    print('  %d/%d ok=%d' % (idx[0], len(entries), len(done)), flush=True)

    ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return done


def main():
    ovf = json.load(open(OVF, encoding='utf-8'))
    # only handle the ones we can win: cap >= 8 bytes
    ent = []
    for f, i, cap, _need in ovf:
        p = os.path.join(TRANS, f + '.json')
        if not os.path.exists(p) or cap < 6:
            continue
        j = json.load(open(p, encoding='utf-8'))
        e = next((x for x in j if x['id'] == i), None)
        if not e:
            continue
        ent.append((f, i, cap, e['pre_jp'], e['message']))
    print('retry candidates: %d' % len(ent))
    done = retry(ent, None)
    print('shortened ok: %d' % len(done))
    # write back
    by_file = {}
    for (f, i), t in done.items():
        by_file.setdefault(f, {})[i] = t
    cache = open(os.path.join(TRANS, '_cache.jsonl'), 'a', encoding='utf-8')
    n = 0
    for f, m in by_file.items():
        p = os.path.join(TRANS, f + '.json')
        j = json.load(open(p, encoding='utf-8'))
        for e in j:
            if e['id'] in m:
                e['message'] = m[e['id']]
                cache.write(json.dumps({'k': '%s:%d' % (f, e['id']), 't': m[e['id']]},
                                       ensure_ascii=False) + '\n')
                n += 1
        json.dump(j, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    cache.close()
    print('updated %d entries in trans/' % n)


if __name__ == '__main__':
    main()
