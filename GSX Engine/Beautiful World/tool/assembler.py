#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assembler.py — 语义汇编器（GSX VM / The Beautiful World）
==========================================================
拖放即用：把 `*.asm.txt`（或存放它们的目录）拖到本脚本图标上，
即生成重建后的二进制（目录输入时批量输出到 `<目录>_bin/`）。
命令行：

    python assembler.py <asm.txt | asm目录> [-o <输出文件|输出目录>] [--encoding cp932]

输入目录时按扩展名 `*.asm.txt` 批量汇编，输出 `<脚本名>.bin`
（内容 = code 区 + data 池，可用 `cod.build_cod` 重新组装成 CoD.cpt）。

本汇编器与 `disassembler.py` 配对，保证 disasm → asm 逐字节一致。
"""
import os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gsx_asm as A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('-o', '--out')
    ap.add_argument('--encoding', default='cp932')
    a = ap.parse_args()
    A.ENC = a.encoding
    if os.path.isdir(a.input):
        out = a.out or (a.input.rstrip('/\\') + '_bin')
        os.makedirs(out, exist_ok=True)
        n = 0
        for fn in sorted(os.listdir(a.input)):
            if not fn.endswith('.asm.txt'):
                continue
            info, code, pool = A.assemble(os.path.join(a.input, fn))
            open(os.path.join(out, fn[:-8] + '.bin'), 'wb').write(code + pool)
            n += 1
        print('[assembler] %d scripts -> %s' % (n, out))
    else:
        out = a.out or (a.input[:-8] + '.rebuild.bin')
        info, code, pool = A.assemble(a.input)
        open(out, 'wb').write(code + pool)
        print('[assembler] %s -> %s (%d code bytes + %d pool bytes)'
              % (a.input, out, len(code), len(pool)))


if __name__ == '__main__':
    main()
