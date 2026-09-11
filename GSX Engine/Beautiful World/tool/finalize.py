#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finalize.py — turn the raw LLM cache into the injectable zh.json.

    python finalize.py <jp.json> --cache <cache.json> -o <zh.json>

The cache holds the RAW model output, so re-running this step after tuning the
post-processing never costs a single API call.
"""
import os, sys, json, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from translate import HanziMap, normalise_markers, fix_punct, cp932_clean
from names_map import build_name_pairs, apply_names, SRC_OVERRIDE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jp_json')
    ap.add_argument('--cache', required=True)
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()

    hm = HanziMap(os.path.join(ROOT, 'hanzi2kanji_table.txt'),
                  os.path.join(HERE, 'jis_supplement.txt'))
    nm = build_name_pairs(hm)
    entries = json.load(open(a.jp_json, encoding='utf-8'))
    cache = json.load(open(a.cache, encoding='utf-8')) if os.path.exists(a.cache) else {}
    dropped = collections.Counter()
    same = 0
    uniq_src = set()
    n = 0
    for e in entries:
        src = e['pre_jp']
        uniq_src.add(src)
        if src in SRC_OVERRIDE:            # whole-string name-table entry
            t = SRC_OVERRIDE[src]
        else:
            t = cache.get(src)
        if not t:
            e['message'] = src
            continue
        t = normalise_markers(t)
        t = hm.apply(t)
        t = apply_names(t, nm)
        t = fix_punct(t)
        t, d = cp932_clean(t)
        for c in d:
            dropped[c] += 1
        if t == src:
            same += 1
        e['message'] = t
        n += 1
    json.dump(entries, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[finalize] %d/%d entries translated (%d unique sources), %d unchanged'
          % (n, len(entries), len(uniq_src), same))
    if dropped:
        print('[finalize] dropped non-cp932 chars:', dict(dropped.most_common(30)))
    else:
        print('[finalize] no non-cp932 characters left')


if __name__ == '__main__':
    main()
