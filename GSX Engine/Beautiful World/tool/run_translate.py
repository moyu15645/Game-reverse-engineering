#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the translation for every chapter sequentially (resumable via cache)."""
import os, sys, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

CHAPTERS = [('ch1', 'Beautiful World 1.1'), ('ch2', 'Beautiful World 1.2'),
            ('ch3', 'Beautiful World 1.3')]

for ch, folder in CHAPTERS:
    src = os.path.join(ROOT, 'work', ch, 'text', 'jp.json')
    out = os.path.join(ROOT, 'work', ch, 'text', 'zh.json')
    cache = os.path.join(ROOT, 'work', ch, 'text', 'cache.json')
    print('=== %s  %s' % (ch, time.strftime('%H:%M:%S')), flush=True)
    cmd = [PY, '-u', os.path.join(HERE, 'translate.py'), src, '-o', out,
           '--cache', cache, '--batch', '120', '--workers', '10']
    r = subprocess.run(cmd)
    print('=== %s done rc=%d  %s' % (ch, r.returncode, time.strftime('%H:%M:%S')), flush=True)
print('ALL DONE', flush=True)
