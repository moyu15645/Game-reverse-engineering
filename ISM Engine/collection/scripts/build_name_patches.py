# -*- coding: utf-8 -*-
"""扫描 _text/*.json，收集唯一 (name, name_offset)，按 _work/name_zh.py 翻译，
经 to_kanji 转日繁后编码 cp932，存入 _work/translation_cache.json（name 池专用）。"""
import json, glob, os, sys
sys.path.insert(0, 'ism_tools')
from translate import to_kanji, load_kanji_table
sys.path.insert(0, '_work')
from name_zh import NAME_ZH

CACHE = '_work/translation_cache.json'
cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
table = load_kanji_table()

pairs = {}
for fp in sorted(glob.glob('_text/*.json')):
    for r in json.load(open(fp, encoding='utf-8')):
        n = r.get('name') or ''
        off = r.get('name_offset') or ''
        if n and off and off not in pairs:
            pairs[off] = n

translated = cp932_fail = dict_miss = 0
unmapped_chars = set()
for off, name in pairs.items():
    zh = NAME_ZH.get(name) or NAME_ZH.get(name.strip())
    if zh is None:
        dict_miss += 1
        continue
    kanji, unm = to_kanji(zh, table)
    unmapped_chars.update(unm)
    if unm:
        print('  未覆盖字符:', repr(zh), '->', repr(kanji), 'unmapped:', unm)
    try:
        b = kanji.encode('cp932')
    except Exception as e:
        cp932_fail += 1
        print('  cp932 仍失败:', repr(kanji), e)
        continue
    cache[off] = {'name_zh': zh, 'name_kanji': kanji, 'name_zh_bytes_hex': b.hex()}
    translated += 1

json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'翻译 name: {translated} | dict 缺失: {dict_miss} | cp932 仍失败: {cp932_fail}')
print(f'未覆盖字符: {"".join(sorted(unmapped_chars)) if unmapped_chars else "无"}')
