#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_echo.py — 修复"原样照抄日文"的条目

模型偶尔会把某些条目原样回显（短句、拟声、呻吟、残缺句尤甚）。本工具：
  1. 找出所有 `message == pre_jp` 且原文含假名的条目；
  2. 用更强硬的提示词（禁止照抄）分小批重试；
  3. 对仍失败的条目再逐条重试一次，最终写入缓存。

    python retry_echo.py <jp.json> --cache <cache.json> [--batch 16]
"""
import os, sys, json, argparse, re
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from translate import translate_batch

KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')

SYS = ('你是日译中翻译。下面 JSON 数组里是日文台词，必须逐条翻译成简体中文，'
       '绝对禁止原样照抄日文、禁止保留假名。'
       '短句、拟声词、呻吟、口头禅也要译成中文（拟声可音译，如 ひ→咿、ぁ→啊）。'
       '保留 [[BR]]、[[SP]] 标记与人名。输出等长 JSON 数组，只输出 JSON。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jp_json')
    ap.add_argument('--cache', required=True)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()

    entries = json.load(open(a.jp_json, encoding='utf-8'))
    cache = json.load(open(a.cache, encoding='utf-8')) if os.path.exists(a.cache) else {}

    todo = []
    seen = set()
    for e in entries:
        s = e['pre_jp']
        if s in seen or not KANA.search(s):
            continue
        seen.add(s)
        t = cache.get(s)
        if not t or t == s:
            todo.append(s)
    print('[echo] %d unique strings need re-translation' % len(todo), flush=True)
    if not todo:
        return

    batches = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
    lock = __import__('threading').Lock()
    fixed = [0]

    def work(b):
        r = translate_batch(b, system=SYS)
        if not r:
            return
        with lock:
            for s, t in zip(b, r):
                if t and t != s:
                    cache[s] = t
                    fixed[0] += 1

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, batches))
    json.dump(cache, open(a.cache, 'w', encoding='utf-8'), ensure_ascii=False)
    print('[echo] pass1 fixed %d / %d' % (fixed[0], len(todo)), flush=True)

    # round 2 : one by one
    left = [s for s in todo if not cache.get(s) or cache.get(s) == s]
    print('[echo] round2 singles: %d' % len(left), flush=True)
    f2 = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(lambda s: work([s]), left))
    json.dump(cache, open(a.cache, 'w', encoding='utf-8'), ensure_ascii=False)
    left = [s for s in todo if not cache.get(s) or cache.get(s) == s]
    print('[echo] still unresolved: %d' % len(left), flush=True)
    if left:
        print('   e.g.', left[:6])


if __name__ == '__main__':
    main()
