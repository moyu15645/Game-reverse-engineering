"""LOVELESS .OVL 汇编器（与 disassembler.py 严格互逆，零突变）

支持指令：
  .encoding <codec>          声明字符串编码
  loc_XXXXXXXX:              标签（= 该位置在文件中的字节偏移）
  TEXT "…"                   文本区，编码为 <codec> 的字节
  .byte 0xNN, …              原样字节
  ; 注释 / 空行              忽略

TEXT 长度变化时，其后 .byte 段顺次平移；标签按新的字节位置重新计算，
所有引用标签的场合（本项目未使用）不受影响。

用法:
  python assembler.py <file.asm.txt> [-o out.bin] [--encoding cp932]
"""
import argparse
import glob
import os
import re
import sys

ENC = 'cp932'


def unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i + 1]
            if n == '\\':
                out.append('\\')
            elif n == '"':
                out.append('"')
            elif n == 'n':
                out.append('\n')
            elif n == 't':
                out.append('\t')
            else:
                out.append(n)
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


TEXT_RE = re.compile(r'^TEXT\s+"((?:[^"\\]|\\.)*)"\s*$')
BYTE_RE = re.compile(r'^\.byte\s+(.+)$')


def assemble(text, enc=ENC):
    data = bytearray()
    label_pos = {}
    pending = []          # (kind, payload, label_anchor)
    lines = text.splitlines()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('.encoding'):
            enc = line.split(None, 1)[1].strip()
            continue
        if line in ('.end', '.org'):
            continue
        m = re.match(r'^(loc_[0-9A-Fa-f]+):$', line)
        if m:
            pending.append(('label', m.group(1), len(data)))
            continue
        m = TEXT_RE.match(line)
        if m:
            s = unescape(m.group(1))
            data += s.encode(enc)
            continue
        m = BYTE_RE.match(line)
        if m:
            for tok in m.group(1).split(','):
                tok = tok.strip()
                if not tok:
                    continue
                data.append(int(tok, 16) if tok.lower().startswith('0x') else int(tok))
            continue
        raise ValueError('unparsable line: %r' % raw)
    for kind, name, p in pending:
        label_pos[name] = p
    return bytes(data), label_pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--encoding', default=ENC)
    ap.add_argument('--outdir')
    a = ap.parse_args()
    if os.path.isdir(a.input):
        files = sorted(f for f in glob.glob(os.path.join(a.input, '*'))
                       if f.endswith('.asm.txt'))
        outdir = a.outdir or (a.input.rstrip('/\\') + '_bin')
        os.makedirs(outdir, exist_ok=True)
        for f in files:
            txt = open(f, encoding='utf-8').read()
            blob, _ = assemble(txt, a.encoding)
            base = os.path.basename(f)[:-len('.asm.txt')]
            open(os.path.join(outdir, base + '.OVL'), 'wb').write(blob)
        print('assembled %d files -> %s' % (len(files), outdir))
    else:
        txt = open(a.input, encoding='utf-8').read()
        blob, _ = assemble(txt, a.encoding)
        outp = a.output or (a.input[:-len('.asm.txt')] + '.rebuild' if a.input.endswith('.asm.txt')
                            else a.input + '.rebuild')
        open(outp, 'wb').write(blob)
        print('assembled %s -> %s (%d bytes)' % (a.input, outp, len(blob)))


if __name__ == '__main__':
    main()
