#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
names_map.py — force kana-written proper nouns to their agreed Chinese form.

The very first translation pass was run with a system prompt that said
"人名保持原样不译" (keep proper nouns verbatim), so names such as らいん /
セカンド / ひぃちゃん / ぴよちゃん / オーキ stayed in kana inside otherwise
Chinese sentences.  Re-running the whole 28 207-line corpus just for that is
wasteful, so the names are repaired deterministically here instead.

`NAMES` in translate.py is the single source of truth.  The replacement is done
on the **post-HanziMap** text, so the Chinese side is itself pushed through the
mapping first and therefore cancels out to the correct code points.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from translate import NAMES

# variants some earlier model runs produced for the same characters
EXTRA = [
    ('ぴよ醤', '小阳'),
    ('ぴよ酱', '小阳'),
    ('小ぴよ', '小阳'),
    ('小鯲', '小阳'),
    ('小鳅', '小阳'),
    ('小鶏醤', '小阳'),
    ('小雛醤', '小阳'),
    ('ぴよ', '小阳'),
    ('霧谷ひ', '霧谷希'),
    ('雾谷ひ', '霧谷希'),
    ('ときの醤', '小鴇乃'),
    ('ときのだ', '小鴇乃'),
    ('ときの', '小鴇乃'),
    ('たかなし', '小鳥游'),
    ('くるみん', '来未'),
    ('くるみ', '来未'),
    ('れーくん', '玲君'),
    ('しーか', '詩歌'),
    ('おユユ', '小柚'),
    # honorifics that the first pass left in kana (the model itself had already
    # settled on 酱 for ちゃん and 君 for くん elsewhere, so keep those)
    ('如月さん', '如月同学'),
    ('藤水さん', '藤水同学'),
    ('告春さん', '告春同学'),
    ('敷縞さん', '敷縞同学'),
    ('鴇乃さん', '鴇乃同学'),
    ('時乃醤', '小鴇乃'),
    ('小鳥游さん', '小鳥游同学'),
    ('羽野真さん', '羽野真老师'),
    ('隆司さん', '隆司先生'),
    ('玲子さん', '玲子小姐'),
    ('くるみさん', '来未小姐'),
    ('さん', '同学'),
    ('ちゃん', '醤'),
    ('くん', '君'),
    # leftover jargon / shop & site names
    ('ペーパーバッグ', '纸袋人'),
    ('バッグ', '包'),
    ('ホメオスタシスリンク', '恒常性链接'),
    ('アネモネ', '银莲花'),
    ('ゼミ', '研讨班'),
    ('ミスケース', '失误案例'),
    ('酎ハイ', '烧酒'),   # 酎 的码位被占用，只能意译
    ('タカシタ', '高下'),
    ('タカジー', '高次'),
    ('ゆーかり', '由加里'),
    # stray Japanese emphasis marks (small tsu) — never valid in Chinese
    ('っ', ''),
    ('ッ', ''),
]


# Entries of the speaker-name table whose *whole string* is special.  These are
# keyed by the Japanese source so they survive any cache rebuild.
SRC_OVERRIDE = {
    'らいん': '莱茵',            # the name tag used to come out as "lin"
    '学生A': '学生A', '学生B': '学生B', '学生C': '学生C', '学生D': '学生D',
    '女子大生１': '女大学生１', '女子大生２': '女大学生２',
    '女子大生３': '女大学生３', '女子大生たち': '女大学生们',
}


def build_name_pairs(hm):
    pairs = [(jp, hm.apply(cn)) for jp, cn in NAMES]
    pairs += [(a, hm.apply(b)) for a, b in EXTRA]
    # longest first so ツグハル is handled before ハル, ときのちゃん before ときのん
    pairs.sort(key=lambda x: -len(x[0]))
    return pairs


def apply_names(text, pairs):
    for a, b in pairs:
        if a in text:
            text = text.replace(a, b)
    return text


def residual_kana(text):
    """kana that are almost certainly a left-over name (not a punctuation mark)"""
    bad = []
    for i, c in enumerate(text):
        o = ord(c)
        if 0x3040 <= o <= 0x309f or 0x30a0 <= o <= 0x30fa:
            bad.append(c)
    return ''.join(bad)


if __name__ == '__main__':
    import json
    from translate import HanziMap
    ROOT = os.path.dirname(HERE)
    hm = HanziMap(os.path.join(ROOT, 'hanzi2kanji_table.txt'),
                  os.path.join(HERE, 'jis_supplement.txt'))
    pairs = build_name_pairs(hm)
    for a, b in pairs:
        print('%s -> %s' % (a, b))
