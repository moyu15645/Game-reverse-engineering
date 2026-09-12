"""LOVELESS 汉化版构建 + 安全校验

流程:
  1. inject（可选 hanzi2kanji 映射）: unpack/ + trans/  -> build/unpack_cn/
  2. 安全校验: 逐文件对比，确认【所有被改动的字节都落在文本区内】
  3. pack: build/unpack_cn/ -> build/DATA.HED + build/DATA.DAT
  4. 汇总统计与溢出清单

用法:
  python tool/build.py [--no-map] [--outdir build]
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, 'tool'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-map', action='store_true', help='不套用 hanzi2kanji 映射')
    ap.add_argument('--outdir', default='build')
    ap.add_argument('--srcdir', default='unpack')
    ap.add_argument('--transdir', default='trans')
    a = ap.parse_args()

    outdir = os.path.join(PROJ, a.outdir)
    patched = os.path.join(outdir, 'unpack_cn')
    os.makedirs(outdir, exist_ok=True)
    if os.path.isdir(patched):
        shutil.rmtree(patched)

    import extract
    print('== step 1: inject ==')
    extract.do_inject(os.path.join(PROJ, a.srcdir), os.path.join(PROJ, a.transdir),
                      patched, use_map=not a.no_map)

    print()
    print('== step 2: safety check (all changed bytes must be inside text runs) ==')
    from ovl_text import scan_runs
    nfile = nchanged = nbad = 0
    bad_files = []
    seen = set()
    for p in sorted(glob.glob(os.path.join(PROJ, a.srcdir, '*'))):
        nm = os.path.basename(p)
        if not nm.lower().endswith('.ovl') or nm.lower() in seen:
            continue
        seen.add(nm.lower())
        q = os.path.join(patched, nm)
        if not os.path.exists(q):
            continue
        x = open(p, 'rb').read()
        y = open(q, 'rb').read()
        if len(x) != len(y):
            bad_files.append((nm, 'size changed'))
            nbad += 1
            continue
        allowed = set()
        for r in scan_runs(x):
            allowed.update(range(r['start'], r['start'] + r['length']))
        diff = [i for i in range(len(x)) if x[i] != y[i]]
        if diff:
            nchanged += 1
        outside = [i for i in diff if i not in allowed]
        if outside:
            nbad += 1
            bad_files.append((nm, len(outside), hex(outside[0])))
        nfile += 1
    print('files checked : %d' % nfile)
    print('files changed : %d' % nchanged)
    print('VIOLATIONS    : %d  (bytes changed outside text runs / size drift)' % nbad)
    for b in bad_files[:10]:
        print('   ', b)

    print()
    print('== step 3: repack ==')
    import hed_tool
    # pack() locates the archive manifest inside srcdir; put it alongside the
    # modified .ovl files and read all untouched resources from a.srcdir.
    shutil.copy2(os.path.join(PROJ, a.srcdir, '_manifest.json'),
                 os.path.join(patched, '_manifest.json'))
    hed_out = os.path.join(outdir, 'DATA.HED')
    dat_out = os.path.join(outdir, 'DATA.DAT')
    hed_tool.cmd_pack(patched, hed_out, dat_out,
                      extra_dirs=[os.path.join(PROJ, a.srcdir)])

    print()
    print('== summary ==')
    for f in ('DATA.HED', 'DATA.DAT'):
        p = os.path.join(outdir, f)
        print('  %-10s %12d bytes' % (f, os.path.getsize(p)))
    for extra in ('_overflow.json', '_dropped_chars.json'):
        p = os.path.join(patched, extra)
        if os.path.exists(p):
            d = json.load(open(p, encoding='utf-8'))
            print('  %-18s %d records' % (extra, len(d)))


if __name__ == '__main__':
    main()
