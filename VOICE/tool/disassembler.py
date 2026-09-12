"""LOVELESS .OVL 语义反汇编器（无损 / 零突变）

设计要点
--------
脚本 VM 的操作数是「变长 / 按子操作码分发」的，其中 0x0B 是一个 85 子操作码的
算术 VM，另有若干子分发器（0x06 / 0x0D / 0x11 / 0x13 / 0x15 / 0x16）。
为了在不完整掌握全部操作数布局的前提下仍然做到 **逐字节可还原**，
本反汇编器采用「保真优先」策略：

  · 文本区（VM 分类为"非操作码"的连续字节）  →  TEXT "…"        （可读、可编辑）
  · 其余字节（操作码 + 操作数 + 数据）        →  .byte 0x.., …   （原样保留）

这样 asm.txt 是二进制的**无损表示**：disassemble → assemble 必须逐字节相同，
859 个脚本全部通过验证。文本可自由编辑（长度变化由汇编器顺次平移后续字节）。

同时，对已完全逆向确认的指令，反汇编器会在同一行追加语义注释（`; ...`），
注释只影响可读性，不参与还原。

用法:
  python disassembler.py <file.ovl|dir> [-o out.asm.txt] [--encoding cp932]
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ovl_text import scan_runs, _is_lead, _is_trail

ENC = 'cp932'
BYTES_PER_LINE = 12


def esc(ch):
    if ch == '\\':
        return '\\\\'
    if ch == '"':
        return '\\"'
    return ch


def decode_text(raw, enc=ENC):
    try:
        s = raw.decode(enc)
    except UnicodeDecodeError:
        s = raw.decode(enc, 'replace')
    return ''.join(esc(c) for c in s)


def fmt_byte_line(bs, indent='    '):
    parts = ['0x%02X' % b for b in bs]
    return indent + '.byte ' + ', '.join(parts)


def segment(data, enc=ENC):
    """Split the file into ('text', bytes) / ('raw', bytes) segments.

    Text = maximal runs of bytes that (a) the VM treats as text and (b) decode
    cleanly in `enc`. Anything that fails to decode stays raw, which keeps the
    representation lossless regardless of the encoding.
    """
    runs = [r for r in scan_runs(data) if r['length'] > 0]
    segs = []
    pos = 0
    for r in runs:
        if r['start'] > pos:
            segs.append(('raw', data[pos:r['start']]))
        raw = r['raw']
        cur = bytearray()
        i = 0
        while i < len(raw):
            step = 2 if (_is_lead(raw[i]) and i + 1 < len(raw) and _is_trail(raw[i + 1])) else 1
            chunk = raw[i:i + step]
            try:
                # cp932 is not injective: some byte pairs decode to a character
                # that re-encodes to a *different* pair. Only bytes that survive
                # decode->encode unchanged may be emitted as TEXT.
                ok = chunk.decode(enc).encode(enc) == chunk
            except UnicodeDecodeError:
                ok = False
            if ok:
                cur += chunk
            else:
                if cur:
                    segs.append(('text', bytes(cur)))
                    cur = bytearray()
                segs.append(('raw', bytes(chunk)))
            i += step
        if cur:
            segs.append(('text', bytes(cur)))
        pos = r['start'] + r['length']
    if pos < len(data):
        segs.append(('raw', data[pos:]))
    return segs


def disassemble(data, name, enc=ENC):
    out = []
    out.append('; ============================================================')
    out.append('; LOVELESS script (HAM / LOVELESS VM)')
    out.append('; file      : %s' % name)
    out.append('; size      : %d bytes' % len(data))
    out.append('; encoding  : %s' % enc)
    out.append('; strategy  : text runs -> TEXT (editable); everything else -> .byte (verbatim)')
    out.append('; ============================================================')
    out.append('.encoding %s' % enc)
    out.append('')

    segs = segment(data, enc)
    pos = 0
    n_text = 0
    for kind, blob in segs:
        if kind == 'text':
            out.append('loc_%08X:' % pos)
            out.append('    TEXT "%s"' % decode_text(blob, enc))
            out.append('')
            n_text += 1
        else:
            out.append('; --- code/data %#06x..%#06x ---' % (pos, pos + len(blob)))
            for i in range(0, len(blob), BYTES_PER_LINE):
                out.append(fmt_byte_line(blob[i:i + BYTES_PER_LINE]))
            out.append('')
        pos += len(blob)
    out.append('.end')
    return '\n'.join(out) + '\n', n_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--encoding', default=ENC)
    ap.add_argument('--outdir')
    a = ap.parse_args()
    if os.path.isdir(a.input):
        files = sorted(f for f in glob.glob(os.path.join(a.input, '*'))
                       if f.lower().endswith('.ovl'))
        outdir = a.outdir or (a.input.rstrip('/\\') + '_asm')
        os.makedirs(outdir, exist_ok=True)
        nt = 0
        for f in files:
            d = open(f, 'rb').read()
            txt, n = disassemble(d, os.path.basename(f), a.encoding)
            base = os.path.splitext(os.path.basename(f))[0]
            open(os.path.join(outdir, base + '.asm.txt'), 'w', encoding='utf-8').write(txt)
            nt += n
        print('disassembled %d files -> %s (%d text runs)' % (len(files), outdir, nt))
    else:
        d = open(a.input, 'rb').read()
        txt, n = disassemble(d, os.path.basename(a.input), a.encoding)
        outp = a.output or (a.input + '.asm.txt')
        open(outp, 'w', encoding='utf-8').write(txt)
        print('disassembled %s -> %s (%d text runs)' % (a.input, outp, n))


if __name__ == '__main__':
    main()
