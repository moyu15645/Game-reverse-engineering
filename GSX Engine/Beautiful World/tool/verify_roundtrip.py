#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full round-trip verification: disasm -> asm must reproduce every script of
every chapter bit for bit."""
import os, sys, subprocess, tempfile, hashlib, filecmp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cod, fpack, gsx_text as GT

CH = [('ch1', 'Beautiful World 1.1'), ('ch2', 'Beautiful World 1.2'),
      ('ch3', 'Beautiful World 1.3')]

allok = True
for ch, folder in CH:
    chdir = os.path.join(ROOT, folder)
    info = GT.open_chapter(chdir)
    dec = info['dec']
    asmdir = os.path.join(ROOT, 'work', ch, 'asm')
    bindir = os.path.join(ROOT, 'work', ch, 'rebuild_asm')
    subprocess.run([sys.executable, os.path.join(HERE, 'gsx_disasm.py'),
                    chdir, '-o', asmdir], check=True, cwd=ROOT)
    subprocess.run([sys.executable, os.path.join(HERE, 'gsx_asm.py'),
                    asmdir, '-o', bindir], check=True, cwd=ROOT)
    ok = bad = 0
    for s in info['scripts']:
        orig = dec[s['code_off']:s['data_off'] + s['data_len']]
        p = os.path.join(bindir, '%s.bin' % s['name'])
        new = open(p, 'rb').read()
        if new == orig:
            ok += 1
        else:
            bad += 1
            allok = False
            print('  MISMATCH %s/%s: %d vs %d' % (ch, s['name'], len(orig), len(new)))
    print('%s : disasm->asm bit-perfect %d/%d' % (folder, ok, ok + bad))
print('ROUND-TRIP ALL OK' if allok else 'ROUND-TRIP FAILED')
