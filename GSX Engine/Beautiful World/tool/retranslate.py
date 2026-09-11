#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retranslate.py — second-pass re-translation of the lines that still carry kana
after the deterministic name/honorific repair.

    python retranslate.py            # all three chapters
    python retranslate.py ch2        # one chapter

The first pass was run with a prompt that told the model to keep proper nouns
verbatim, so a few hundred lines came back as untouched Japanese (often with
just 僕->仆 style character swaps, which is why an exact `message == pre_jp`
check did not catch them).  Only those lines are sent again, this time with the
corrected system prompt.
"""
import os, sys, json, re
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from translate import translate_batch

KANA = re.compile(r'[\u3041-\u3096\u30a1-\u30fa]')
CH = {'ch1': 'ch1', 'ch2': 'ch2', 'ch3': 'ch3'}


def fix_one(ch, batch=12, workers=6):
    jp = json.load(open(os.path.join(ROOT, 'work', ch, 'text/jp.json'), encoding='utf-8'))
    zh = json.load(open(os.path.join(ROOT, 'work', ch, 'text/zh.json'), encoding='utf-8'))
    cpath = os.path.join(ROOT, 'work', ch, 'text/cache.json')
    cache = json.load(open(cpath, encoding='utf-8'))

    bad = [e['pre_jp'] for e in zh if KANA.search(e['message'])]
    uniq = list(dict.fromkeys(bad))
    print('[%s] %d lines / %d unique still hold kana' % (ch, len(bad), len(uniq)), flush=True)
    if not uniq:
        return
    batches = [uniq[i:i + batch] for i in range(0, len(uniq), batch)]
    got = [0]

    def work(b):
        r = translate_batch(b)
        if r:
            with open(cpath + '.lock', 'w') as _:
                pass
            for s, t in zip(b, r):
                if t and not KANA.search(t):
                    cache[s] = t
            got[0] += 1
            if got[0] % 3 == 0:
                json.dump(cache, open(cpath, 'w', encoding='utf-8'), ensure_ascii=False)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, batches))
    json.dump(cache, open(cpath, 'w', encoding='utf-8'), ensure_ascii=False)
    left = [s for s in uniq if KANA.search(cache.get(s, s))]
    print('[%s] re-translated, %d/%d still kana in cache' % (ch, len(left), len(uniq)), flush=True)


def main():
    for ch in (sys.argv[1:] or ['ch1', 'ch2', 'ch3']):
        fix_one(ch)


if __name__ == '__main__':
    main()
