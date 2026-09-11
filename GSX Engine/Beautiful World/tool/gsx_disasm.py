#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gsx_disasm.py — zero-mutation disassembler for the GSX script VM
("The Beautiful World", Enterbrain 2011)

    python gsx_disasm.py <chapter_dir> -o <outdir> [--encoding cp932] [--raw]
    python gsx_disasm.py <CoD.cpt-decrypted> ...

Output: one `<script>.asm.txt` per scenario script, in the semantic form
defined by docs/vm_analysis.md.  Nothing is ever dumped as raw hex: every byte
is either a printable cp932 character or a `{{XX}}` byte placeholder, and the
assembler (gsx_asm.py) restores the input bit for bit.
"""
import os, sys, json, struct, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod
import opcodelist as OP
import gsx_text as GT

ENC = 'cp932'


# ------------------------------------------------------------------ escaping
def esc_bytes(raw: bytes) -> str:
    """cp932 bytes -> printable, loss-free text ({{XX}} for non-printables)."""
    out = []
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if 0x20 <= b < 0x7F:
            c = chr(b)
            out.append('\\"' if c == '"' else '\\\\' if c == '\\' else c)
            i += 1
            continue
        # try a 2-byte cp932 char (only if it round-trips to the same bytes —
        # cp932 contains duplicate mappings e.g. 0x8790 vs 0x81E0)
        if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC:
            try:
                pair = raw[i:i + 2]
                ch = pair.decode(ENC)
                if (len(ch) == 1 and ord(ch) > 0x2000 and ch not in '"\\'
                        and ch.encode(ENC) == pair):
                    out.append(ch)
                    i += 2
                    continue
            except Exception:
                pass
        if 0xA1 <= b <= 0xDF:                    # half-width katakana
            out.append(raw[i:i + 1].decode(ENC))
            i += 1
            continue
        out.append('{{%02X}}' % b)
        i += 1
    return ''.join(out)


def unesc_bytes(s: str) -> bytes:
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        if s[i] == '\\' and i + 1 < n:
            out += s[i + 1].encode(ENC)
            i += 2
            continue
        if s[i] == '{' and s[i + 1:i + 2] == '{':
            j = s.index('}}', i)
            out.append(int(s[i + 2:j], 16))
            i = j + 2
            continue
        out += s[i].encode(ENC)
        i += 1
    return bytes(out)


# ------------------------------------------------------------------ disassemble
def seg_code(words, disp, arity):
    """Greedy segmentation using the solved arity table; returns list or None."""
    out, i, n = [], 0, len(words)
    while i < n:
        op = words[i] & OP.OP_MASK
        if op not in disp:
            return None
        k = arity.get(op, 0)
        if i + 1 + k > n:
            return None
        out.append((i, op, words[i] >> OP.ARG_SHIFT if False else (words[i] >> OP.ARG_SHIFT),
                    list(words[i + 1:i + 1 + k])))
        i += 1 + k
    return out


def disassemble_script(scr, blob, disp, arity, raw_words=False):
    code = blob[scr['code_off']:scr['code_off'] + scr['code_len']]
    data = blob[scr['data_off']:scr['data_off'] + scr['data_len']]
    pool = {}
    off = 0
    while off < len(data):
        nul = data.find(b'\x00', off)
        if nul < 0:
            break
        pool[off] = data[off:nul]
        off = nul + 1

    L = []
    L.append('; ' + '=' * 74)
    L.append('; GSX VM disassembly  —  The Beautiful World (Enterbrain, 2011)')
    L.append('; script     : %s' % scr['name'])
    L.append('; code_words : %d        (32-bit VM words)' % scr['code_words'])
    L.append('; data_size  : %d        (string pool bytes)' % scr['data_size'])
    L.append('; encoding   : %s' % ENC)
    L.append('; ' + '=' * 74)
    L.append('')
    L.append('.script %s' % scr['name'])
    L.append('.encoding %s' % ENC)
    L.append('.hdr16a ' + ', '.join('0x%02X' % b for b in scr['hdr16a']))
    L.append('.hdr16b ' + ', '.join('0x%02X' % b for b in scr['hdr16b']))
    L.append('.code %d' % scr['code_words'])
    L.append('')

    words = list(struct.unpack_from('<%dI' % scr['code_words'], code, 0))
    ins = None if raw_words else seg_code(words, disp, arity)
    if ins is None:
        for i, w in enumerate(words):
            if i % 16 == 0:
                L.append('')
                L.append('loc_c%08X:' % i)
            L.append('    .word 0x%08X' % w)
    else:
        for (i, op, arg, ops) in ins:
            if i % 16 == 0 or i == 0:
                L.append('')
                L.append('loc_c%08X:' % i)
            name = OP.mnemonic(op)
            if arg:
                name += '@0x%X' % arg
            parts = ['0x%08X' % v for v in ops]
            line = '    %-14s %s' % (name, ', '.join(parts))
            notes = []
            for v in ops:
                if v in pool:
                    notes.append('0x%X="%s"' % (v, esc_bytes(pool[v])[:40]))
            if notes:
                line = '%-52s ; %s' % (line, ' '.join(notes))
            L.append(line)
    L.append('')
    L.append('.end_code')
    L.append('')
    L.append('.data %d' % scr['data_size'])
    L.append('')
    off = 0
    while off < len(data):
        nul = data.find(b'\x00', off)
        if nul < 0:
            L.append('str_%08X: ' % off + '"' + esc_bytes(data[off:]) + '"')
            break
        L.append('str_%08X: "%s"' % (off, esc_bytes(data[off:nul])))
        off = nul + 1
    L.append('')
    L.append('.end_data')
    L.append('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('chapter_dir')
    ap.add_argument('-o', '--outdir', required=True)
    ap.add_argument('--raw', action='store_true')
    a = ap.parse_args()

    ch = GT.open_chapter(a.chapter_dir)
    model = json.load(open(os.path.join(HERE, '_opcodemodel.json'), encoding='utf-8'))
    disp = {int(k): v for k, v in model['dispatch'].items()}
    arity = {int(k): v for k, v in model['arity'].items()}
    os.makedirs(a.outdir, exist_ok=True)
    for s in ch['scripts']:
        txt = disassemble_script(s, ch['dec'], disp, arity, a.raw)
        p = os.path.join(a.outdir, '%s.asm.txt' % s['name'])
        open(p, 'w', encoding='utf-8', newline='\n').write(txt)
    print('[disasm] %d scripts -> %s' % (len(ch['scripts']), a.outdir))


if __name__ == '__main__':
    main()
