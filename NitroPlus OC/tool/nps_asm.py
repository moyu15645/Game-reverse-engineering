#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nps_asm.py —— .nps.asm.txt 汇编器（把语义汇编还原为原始 .nps 二进制）
================================================================================
与 nps_disasm.py 严格配对：反汇编→汇编必须逐字节一致（零突变）。

用法
----
  python nps_asm.py <file.nps.asm.txt|目录> [-o 输出.nps] [--encoding cp932]
  拖放：把 .asm.txt 拖到本脚本上，生成 <名字>_rebuild.nps

规则
----
  · 以 ; 开头的行           → 注释，忽略
  · 以 . 开头的行           → 元信息（.encoding/.eol/.final_nl 等），读入元数据
  · `L<6位行号>  <KIND> <负载>` → 记录行；<KIND> 为 BLK 时负载为空
  · 负载为原始行逐字节内容，按 .eol 拼接，按 .final_nl 决定是否加末尾换行
"""
import argparse
import os
import re
import sys

REC_RE = re.compile(r'^L(\d{6})\s+(\w+)\s?(.*)$')


def asm_one(asm_path, out_path, enc=None):
    meta, recs = {}, []
    with open(asm_path, encoding='utf-8') as f:
        for ln in f.read().split('\n'):
            if ln.startswith(';') or ln == '':
                continue
            if ln.startswith('.'):
                p = ln[1:].split(None, 1)
                meta[p[0]] = p[1].strip() if len(p) > 1 else ''
                continue
            m = REC_RE.match(ln)
            if not m:
                raise ValueError(f'{asm_path}: 无法解析记录行 -> {ln[:90]!r}')
            recs.append((int(m.group(1)), m.group(2), m.group(3)))
    if not recs:
        raise ValueError(f'{asm_path}: 没有任何记录行')
    lines = []
    n = 0
    for idx, kind, payload in recs:
        if idx != n + 1:
            raise ValueError(f'{asm_path}: 行号不连续，期望 {n+1} 得到 {idx}')
        if kind == 'BLK':
            lines.append('')
        elif kind == 'Q':
            payload = payload.strip()
            if not (payload.startswith('"') and payload.endswith('"')):
                raise ValueError(f'{asm_path}: Q 行负载必须为引号字符串 -> {payload[:60]!r}')
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import opcodelist as _OC
            lines.append(_OC.unesc(payload[1:-1]))
        else:
            lines.append(payload)
        n = idx
    eol = {'crlf': '\r\n', 'lf': '\n'}.get(meta.get('eol', 'crlf'))
    if eol is None:
        raise ValueError(f'{asm_path}: .eol={meta.get("eol")} 不受支持（仅 crlf / lf）')
    text = eol.join(lines)
    if meta.get('final_nl', 'yes') == 'yes':
        text += eol
    enc = enc or meta.get('encoding', 'cp932')
    data = text.encode(enc, 'surrogateescape')
    with open(out_path, 'wb') as f:
        f.write(data)
    return data


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('target', nargs='?')
    ap.add_argument('-o', '--output')
    ap.add_argument('--encoding')
    ap.add_argument('-h', '--help', action='store_true')
    if len(sys.argv) == 1 or '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        return
    args = ap.parse_args()
    tgt = args.target
    if os.path.isdir(tgt):
        files = sorted(os.path.join(tgt, f) for f in os.listdir(tgt)
                       if f.lower().endswith('.asm.txt'))
    else:
        files = [tgt]
    for f in files:
        base = os.path.basename(f)
        name = base[:-8] if base.lower().endswith('.asm.txt') else base
        if args.output and len(files) == 1:
            out = args.output
        else:
            out = os.path.join(os.path.dirname(f), name + '.rebuild')
        data = asm_one(f, out, args.encoding)
        print(f'[OK] {f} -> {out}  ({len(data)} 字节)')


if __name__ == '__main__':
    main()
