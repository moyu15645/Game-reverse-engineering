#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disassembler.py — 语义反汇编器（GSX VM / The Beautiful World）
================================================================
拖放即用：把「游戏章目录」或 `CoD.cpt` 的**解密文件**拖到本脚本图标上，
即在同目录生成 `asm/` 与 `*.asm.txt`。
命令行：

    python disassembler.py <章目录|解密后的CoD.cpt> [-o <输出目录>]
                           [--encoding cp932] [--raw]

* 输入为章目录时：自动用该章的 EXE 定位解密表 → 解密 CoD.cpt → 逐脚本反汇编；
* 输入为解密后的 CoD.cpt 时：直接按容器解析（仍会尝试从同目录 EXE 取分发表）；
* `--raw`：不做指令切分，逐字输出 `.word`（永远可无损还原）。

输出保证：纯文本、无十六进制转储、不可显示字节一律 `{{XX}}` 占位，
`assembler.py` 可将其逐字节还原。
"""
import os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod
import fpack
import gsx_disasm as D
import gsx_text as GT


def find_exe(folder):
    for fn in os.listdir(folder):
        if fn.lower().endswith('.exe') and 'uninst' not in fn.lower():
            return os.path.join(folder, fn)
    return None


def run(chapter_dir, outdir, raw=False):
    os.makedirs(outdir, exist_ok=True)
    ch = GT.open_chapter(chapter_dir)
    import json
    model = json.load(open(os.path.join(HERE, '_opcodemodel.json'), encoding='utf-8'))
    disp = {int(k): v for k, v in model['dispatch'].items()}
    arity = {int(k): v for k, v in model['arity'].items()}
    for s in ch['scripts']:
        txt = D.disassemble_script(s, ch['dec'], disp, arity, raw)
        open(os.path.join(outdir, '%s.asm.txt' % s['name']), 'w',
             encoding='utf-8', newline='\n').write(txt)
    print('[disassembler] %d scripts -> %s' % (len(ch['scripts']), outdir))


def main():
    argv = sys.argv[1:]
    if not argv:
        ap = argparse.ArgumentParser()
        ap.add_argument('input')
        ap.print_help()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('input', help='章目录（含 filepack.bin 与 EXE）')
    ap.add_argument('-o', '--outdir')
    ap.add_argument('--encoding', default='cp932')
    ap.add_argument('--raw', action='store_true')
    a = ap.parse_args()
    inp = a.input.rstrip('/\\')
    outdir = a.outdir or os.path.join(inp, 'asm')
    D.ENC = a.encoding
    run(inp, outdir, a.raw)


if __name__ == '__main__':
    main()
