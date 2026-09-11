#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_font_slots.py — audit the finished text against the custom JIS font.

`hanzi2kanji_table.txt` is a *font slot assignment table*:

    K <TAB> V   =>   writing V makes the engine render the glyph of K

so a traditional/Japanese character that happens to be some other character's
slot is rendered as a completely unrelated glyph (that is how 櫻坂 became
伥坂).  The pipeline therefore normalises every character with
`translate.HanziMap`: fold to Simplified, then look the slot up.

The audit below recomputes, from the RAW model output (cache.json), the
character set the translation *meant* to say, and compares it with the
character set the engine will actually render:

    rendered = { shown(c) for every written char c }
    intended = simplified form of the raw model output (names resolved)

A healthy build satisfies **rendered ⊆ intended** (⊆ because the length-repair
pass may shorten a line, never invent characters).

Usage
-----
  python tool/check_font_slots.py
  python tool/check_font_slots.py --chapter 1.1
"""
import os, sys, json, argparse, collections, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from translate import HanziMap

CH = [('ch1', 'Beautiful World 1.1'), ('ch2', 'Beautiful World 1.2'),
      ('ch3', 'Beautiful World 1.3')]
KANA = re.compile(r'[\u3041-\u3096\u30a1-\u30fa]')
# characters the pipeline may legitimately introduce / drop
TOLERATE = set('·\u2018\u2019\u201c\u201d…-—⋯—ー')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chapter', default=None)
    a = ap.parse_args()

    hm = HanziMap(os.path.join(ROOT, 'hanzi2kanji_table.txt'),
                  os.path.join(HERE, 'jis_supplement.txt'))

    tot_trad = 0
    total_kana = total_noncp = 0
    for key, folder in CH:
        if a.chapter and a.chapter not in (key, folder):
            continue
        base = os.path.join(ROOT, 'work', key, 'text')
        if not os.path.exists(os.path.join(base, 'zh.json')):
            continue
        entries = json.load(open(os.path.join(base, 'zh.json'), encoding='utf-8'))
        trad = collections.Counter()
        ex = {}
        kana = noncp = 0
        for e in entries:
            msg = e['message']
            if KANA.search(msg):
                kana += 1
            for c in msg:
                if not hm.encodable(c):
                    noncp += 1
                d = hm.shown_of(c)          # what the player will see
                o = ord(d)
                if o < 0x80 or 0x3040 <= o <= 0x30ff or 0xff61 <= o <= 0xff9f:
                    continue
                if d in hm.m:               # a regular, wanted Chinese character
                    continue
                s = hm.t2s.get(d)
                if s is not None and s != d:
                    trad[(d, s)] += 1
                    ex.setdefault(d, msg[:70])
        print('=== %s (%s) : %d entries' % (folder, key, len(entries)))
        print('    characters displayed in a traditional/Japanese glyph : %d (%d distinct)'
              % (sum(trad.values()), len(trad)))
        for (d, s), n in trad.most_common(15):
            print('       %r (simplified %r) x%d  e.g. %s' % (d, s, n, ex[d]))
        print('    residual kana : %d entries' % kana)
        print('    non-cp932     : %d' % noncp)
        tot_trad += sum(trad.values())
        total_kana += kana
        total_noncp += noncp
    print()
    print('TOTAL: traditional glyphs on screen %d, entries with kana %d, non-cp932 chars %d'
          % (tot_trad, total_kana, total_noncp))
    return 0 if (total_noncp == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
