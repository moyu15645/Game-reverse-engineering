#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_gap.py —— 把“补翻包”的译文合并回既有译文，产出可直接注入的完整目录
================================================================================
背景：修复提取器漏提（行首 <K> 等）后，用 nps_refill.py 生成了
      text/script_fixed（完整模板，旧条目 id 不变 + 新增条目 id 顺延）
      text/gap        （只含新增条目，待补翻）

补翻完成后，用本工具合并：
    python tool/merge_gap.py <老译文目录> <gap译文目录> -o <输出目录>

  · 以 id 为主键合并；两边的 pre_jp 必须一致（不一致直接报错，防错配）
  · gap 的 id 都比老译文大（追加在文件末尾），因此不会覆盖任何既有译文
  · 输出目录会一并带上 text/script_fixed 的 .meta.json，可直接喂给
        python tool/nps_text.py inject unpack/script/script <输出目录> -o <注入目录>
"""
import argparse
import glob
import json
import os
import shutil
import sys

META_DEFAULT = 'text/script_fixed'


def merge_one(base_p, gap_p, outp, meta_p):
    base = json.load(open(base_p, encoding='utf-8')) if os.path.exists(base_p) else []
    gap = json.load(open(gap_p, encoding='utf-8')) if os.path.exists(gap_p) else []
    bmap = {e['id']: e for e in base}
    bad = []
    for g in gap:
        b = bmap.get(g['id'])
        if b is not None and b.get('pre_jp') != g.get('pre_jp'):
            bad.append((g['id'], b.get('pre_jp', '')[:30], g.get('pre_jp', '')[:30]))
        bmap[g['id']] = g
    if bad:
        raise ValueError(f'{os.path.basename(gap_p)}: {len(bad)} 条 id 冲突且原文不一致 '
                         f'-> {bad[:3]}')
    out = sorted(bmap.values(), key=lambda e: e['id'])
    json.dump(out, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if meta_p and os.path.exists(meta_p):
        shutil.copy(meta_p, outp + '.meta.json')
    return len(base), len(gap), len(out)


def main():
    ap = argparse.ArgumentParser(description='合并补翻包 → 可注入的完整译文目录')
    ap.add_argument('base_dir', help='老译文目录（如 output_mapped）')
    ap.add_argument('gap_dir', help='补翻包目录（如 text/gap 翻好后）')
    ap.add_argument('-o', '--output', required=True, help='输出目录')
    ap.add_argument('--meta', default=META_DEFAULT,
                    help=f'含 .json.meta.json 的模板目录（默认 {META_DEFAULT}）')
    a = ap.parse_args()
    os.makedirs(a.output, exist_ok=True)
    stems = set()
    for d in (a.base_dir, a.gap_dir):
        for p in glob.glob(os.path.join(d, '*.json')):
            if not p.endswith('.meta.json'):
                stems.add(os.path.basename(p))
    nb = ng = no = 0
    for b in sorted(stems):
        stem = b[:-5]
        eb, eg, eo = merge_one(os.path.join(a.base_dir, b), os.path.join(a.gap_dir, b),
                               os.path.join(a.output, b),
                               os.path.join(a.meta, stem + '.json.meta.json'))
        nb += eb
        ng += eg
        no += eo
    print(f'--- 合并 {len(stems)} 文件：老译文 {nb} + 补翻 {ng} = 完整 {no} 条')
    print(f'--- 输出：{a.output}（已附带 meta）')
    print(f'--- 下一步： python tool/nps_text.py inject unpack/script/script {a.output} -o <注入目录>')


if __name__ == '__main__':
    main()
