#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derive GSX operand arity k(opcode) from the exe, then validate by segmenting
every script of every chapter (the word count must be consumed exactly)."""
import os, sys, re, json, struct, bisect, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod_probe as C

FETCH = 0x466700
GHIDRA_FUNCS = (r'C:\Users\Lenovo\.workbuddy\projects\e-storyline-Beautiful World'
                r'\01c6108b-ab86-48b7-9a3c-914758a31faa\tool-results'
                r'\mcp-ghidra-mcp-list_functions-1789058759986-639d32.txt')

CH = {
    '1.1': ('Beautiful World 1.1', 'Beautiful World １章.exe'),
    '1.2': ('Beautiful World 1.2', 'Beautiful World ２章.exe'),
    '1.3': ('Beautiful World 1.3', 'Beautiful World 最終章.exe'),
}

# arity confirmed by decompiling the handler with Ghidra (see docs/vm_analysis.md)
HAND_VERIFIED = {
    0: 0,     # NOP                  FUN_00460530
    114: 1,   # MESSAGE              FUN_00460700 -> FUN_00460620
    290: 1,   # MESSAGE_MODE         FUN_00460580
    296: 1,   # MSG_ID               FUN_004607f0
    82: 4,    # SPRITE_SHOW          FUN_00462a90
    84: 2,    # FUN_00458b60
    120: 0,   # RETURN / VM-exit     FUN_00458f00
}


def load_funcs():
    funcs = []
    for l in open(GHIDRA_FUNCS).read().split('\n'):
        if ' at ' in l:
            n, a = l.rsplit(' at ', 1)
            funcs.append((int(a, 16), n))
    funcs.sort()
    return funcs


def derive(exe_path):
    exe = open(exe_path, 'rb').read()
    calls = collections.defaultdict(set)
    fetch = collections.Counter()
    for m in re.finditer(rb'\xe8', exe):
        fo = m.start()
        rel = struct.unpack_from('<i', exe, fo + 1)[0]
        va = 0x401000 + (fo - 0x400)
        tgt = va + 5 + rel
        if not (0x401000 <= tgt < 0x4c6200):
            continue
        calls[va].add(tgt)
        if tgt == FETCH:
            fetch[va] += 1
    funcs = load_funcs()
    starts = [f[0] for f in funcs]

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        return funcs[i][0] if i >= 0 else None

    dc = collections.Counter()
    for va, c in fetch.items():
        o = owner(va)
        if o:
            dc[o] += c
    fc = collections.defaultdict(set)
    for va, ts in calls.items():
        o = owner(va)
        if o:
            fc[o].update(ts)

    disp = {int(k): v for k, v in json.load(open(os.path.join(HERE, 'gsx_dispatch.json'))).items()}
    HANDLERS = set(v for v in disp.values() if v)
    memo = {}

    def trans(f, depth=0):
        if f in memo:
            return memo[f]
        if depth > 8:
            return 0
        memo[f] = 0
        t = dc.get(f, 0)
        for c in fc.get(f, ()):
            if c in HANDLERS or c == FETCH:
                continue
            t += trans(c, depth + 1)
        memo[f] = t
        return t

    k = {}
    for op, h in disp.items():
        k[op] = trans(h)
    k.update(HAND_VERIFIED)
    return k, disp


def segment(words, k, disp):
    out, i, n = [], 0, len(words)
    while i < n:
        op = words[i] & 0xffff
        if op not in disp:
            raise ValueError('unknown opcode %d at word %d' % (op, i))
        kk = k.get(op, 0)
        if i + 1 + kk > n:
            raise ValueError('overrun at word %d (op %d k %d)' % (i, op, kk))
        out.append((i, op, list(words[i + 1:i + 1 + kk])))
        i += 1 + kk
    return out


def main():
    allk = {}
    for ch, (folder, exe) in CH.items():
        exe_path = os.path.join(folder, exe)
        k, disp = derive(exe_path)
        print('=== chapter %s : %d opcodes in table' % (ch, len(disp)))
        print('    sample arity:', {o: k[o] for o in (0, 1, 4, 7, 82, 84, 114, 120, 290, 296)})
        tbl = C.load_table(exe_path)
        raw, _ = C.get_file(os.path.join(folder, 'filepack.bin'), '/Data/script/CoD.cpt')
        d = C.decrypt(raw, tbl)
        magic, count, scripts = C.parse_cod(d)
        ok = bad = 0
        fails = []
        for s in scripts:
            w = list(struct.unpack_from('<%dI' % s['f10'], d, s['part1_off']))
            try:
                segment(w, k, disp)
                ok += 1
            except ValueError as e:
                bad += 1
                fails.append((s['name'], str(e)))
        print('    validated scripts: ok=%d bad=%d' % (ok, bad))
        for f in fails[:12]:
            print('      ', f)
        allk[ch] = {str(o): v for o, v in k.items()}
    json.dump(allk, open(os.path.join(HERE, '_arity.json'), 'w'))


if __name__ == '__main__':
    main()
