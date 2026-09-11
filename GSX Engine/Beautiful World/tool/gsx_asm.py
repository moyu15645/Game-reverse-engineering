#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gsx_asm.py — assembler for the GSX script VM (companion of gsx_disasm.py)

    python gsx_asm.py <in.asm.txt> -o <out.bin>
    python gsx_asm.py <asm_dir>    -o <out_dir>          # batch

Reads the semantic listing and rebuilds the exact 32-bit word stream plus the
string pool, so that  disasm -> asm  is bit-perfect.
"""
import os, sys, re, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import opcodelist as OP
from gsx_disasm import unesc_bytes

ENC = 'cp932'


class AsmError(Exception):
    pass


def parse(lines):
    out = {'script': None, 'encoding': ENC, 'code_len': None, 'data_len': None,
           'hdr16a': b'\x00' * 16, 'hdr16b': b'\x00' * 16, 'words': [], 'pool': []}
    mode = None
    for ln, raw in enumerate(lines, 1):
        line = raw.rstrip('\n')
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('.script'):
            out['script'] = s.split(None, 1)[1].strip()
        elif s.startswith('.encoding'):
            out['encoding'] = s.split(None, 1)[1].strip()
        elif s.startswith('.hdr16a'):
            out['hdr16a'] = bytes(int(x, 16) for x in
                                  s.split(None, 1)[1].split(','))
        elif s.startswith('.hdr16b'):
            out['hdr16b'] = bytes(int(x, 16) for x in
                                  s.split(None, 1)[1].split(','))
        elif s.startswith('.code'):
            mode = 'code'
            out['code_len'] = int(s.split()[1])
        elif s.startswith('.end_code'):
            mode = None
        elif s.startswith('.data'):
            mode = 'data'
            out['data_len'] = int(s.split()[1])
        elif s.startswith('.end_data'):
            mode = None
        elif s.endswith(':'):
            continue                       # label
        elif mode == 'code':
            body = s.split(';', 1)[0].strip()
            if not body:
                continue
            if body.startswith('.word'):
                for tok in body.split(None, 1)[1].split(','):
                    out['words'].append(int(tok.strip(), 0))
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)'
                         r'(?:@(0x[0-9A-Fa-f]+|\d+))?\s*(.*)$', body)
            if not m:
                raise AsmError('line %d: cannot parse instruction: %r' % (ln, body))
            name, arg, rest = m.group(1), m.group(2), m.group(3)
            if name not in OP.BY_NAME:
                raise AsmError('line %d: unknown mnemonic %s' % (ln, name))
            op = OP.BY_NAME[name]
            inline = int(arg, 0) if arg else 0
            if inline > 0xFFFF:
                raise AsmError('line %d: inline argument overflow' % ln)
            out['words'].append(((inline & 0xFFFF) << OP.ARG_SHIFT) | (op & OP.OP_MASK))
            if rest:
                for tok in rest.split(','):
                    tok = tok.strip()
                    if tok:
                        out['words'].append(int(tok, 0))
        elif mode == 'data':
            body = s.split(';', 1)[0].strip()
            if not body:
                continue
            m = re.match(r'^(str_[0-9A-Fa-f]+):\s*"(.*)"\s*$', body)
            if not m:
                raise AsmError('line %d: cannot parse data entry: %r' % (ln, body))
            out['pool'].append((m.group(1), unesc_bytes(m.group(2))))
        else:
            raise AsmError('line %d: unexpected content outside a section: %r' % (ln, s))
    return out


def assemble(path):
    a = parse(open(path, encoding='utf-8').read().split('\n'))
    if a['code_len'] is not None and len(a['words']) != a['code_len']:
        raise AsmError('%s: code length mismatch: %d words emitted, header says %d'
                       % (path, len(a['words']), a['code_len']))
    pool = b''.join(raw + b'\x00' for label, raw in a['pool'])
    if a['data_len'] is not None and len(pool) != a['data_len']:
        raise AsmError('%s: data length mismatch: %d bytes emitted, header says %d'
                       % (path, len(pool), a['data_len']))
    body = b''.join(__import__('struct').pack('<I', w) for w in a['words'])
    return a, body, pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    if os.path.isdir(a.input):
        os.makedirs(a.out, exist_ok=True)
        n = 0
        for fn in sorted(os.listdir(a.input)):
            if not fn.endswith('.asm.txt'):
                continue
            info, code, pool = assemble(os.path.join(a.input, fn))
            open(os.path.join(a.out, fn[:-8] + '.bin'), 'wb').write(code + pool)
            n += 1
        print('[asm] %d scripts -> %s' % (n, a.out))
    else:
        info, code, pool = assemble(a.input)
        open(a.out, 'wb').write(code + pool)
        print('[asm] %s -> %s (%d code bytes + %d pool bytes)'
              % (a.input, a.out, len(code), len(pool)))


if __name__ == '__main__':
    main()
