#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_release.py — inject the translation into CoD.cpt and repack filepack.bin.

    python build_release.py ch1 --json work/ch1/text/zh.json \
        --meta work/ch1/text/jp.json.meta.json \
        --src "Beautiful World 1.1" --outdir release/"Beautiful World 1.1"

Steps
  1. read  <src>/filepack.bin, decrypt CoD.cpt
  2. write the translated pool strings back at their ORIGINAL offsets
  3. re-encrypt CoD.cpt
  4. rebuild filepack.bin (records + name table + 16-byte aligned data blocks),
     streaming the untouched files straight through
  5. copy the rest of the game folder (exe, readme, Data/, SaveData/)
"""
import os, sys, json, struct, shutil, argparse, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cod
import fpack
import gsx_text as GT

COD_PATH = '/Data/script/CoD.cpt'
SIG = b'Dm-No-FilePlusD\x00'


def build(src_dir, json_path, meta_path, outdir, force=True, dry=False):
    exe = None
    for fn in os.listdir(src_dir):
        if fn.lower().endswith('.exe') and 'uninst' not in fn.lower():
            exe = os.path.join(src_dir, fn)
    src_pack = os.path.join(src_dir, 'filepack.bin')

    # --- 1/2/3 : inject into CoD.cpt -------------------------------------
    ch = GT.open_chapter(src_dir)
    d = bytearray(ch['dec'])
    trans = {e['id']: (e.get('message') or '')
             for e in json.load(open(json_path, encoding='utf-8'))}
    meta = json.load(open(meta_path, encoding='utf-8'))['entries']
    smap = {s['name']: s for s in ch['scripts']}
    written = overflow = missing = encfail = 0
    over_list = []
    for m in meta:
        t = trans.get(m['id'])
        if not t:
            missing += 1
            continue
        pre = bytes(m['prefix'])
        suf = bytes(m['suffix'])
        try:
            # the speaker-name table (init_app) is a plain string list that the
            # engine never runs through the message parser; the original file
            # stores bare ASCII there (学生A), so do not add the 0x19 prefix
            core = (GT.from_display_raw if m.get('script') == 'init_app'
                    else GT.from_display)(t)
        except UnicodeEncodeError:
            encfail += 1
            continue
        new = pre + core + suf
        if len(new) + 1 > m['slot']:
            overflow += 1
            over_list.append((m['id'], m['script'], len(new), m['slot']))
            if force:
                room = m['slot'] - 1 - len(pre) - len(suf)
                core = core[:max(0, room)]
                while core:
                    try:
                        core.decode('cp932')
                        break
                    except UnicodeDecodeError:
                        core = core[:-1]
                new = pre + core + suf
            else:
                continue
        s = smap[m['script']]
        base = s['data_off'] + m['offset']
        d[base:base + m['slot']] = new + b'\x00' * (m['slot'] - len(new))
        written += 1
    new_cod = cod.encrypt(bytes(d), ch['tbl'], ch['inv'])
    print('[inject] written=%d  missing=%d  overflow=%d  encfail=%d  (%d -> %d bytes)'
          % (written, missing, overflow, encfail, len(ch['raw']), len(new_cod)))
    for o in over_list[:15]:
        print('         overflow id=%d %s need=%d slot=%d' % o)

    # --- 4 : rebuild filepack.bin ----------------------------------------
    with open(src_pack, 'rb') as f:
        head = f.read(32)
        meta_size = struct.unpack_from('<III', head, 0)[2]
        f.seek(0)
        info = fpack.read_named(f.read(meta_size))
    names = [e['name'] for e in info['entries']]
    name_to_blob = {}
    with open(src_pack, 'rb') as f:
        for e in info['entries']:
            if e['name'] == COD_PATH:
                name_to_blob[e['name']] = new_cod
            else:
                f.seek(e['off'])
                name_to_blob[e['name']] = f.read(e['size'])

    os.makedirs(outdir, exist_ok=True)
    out_pack = os.path.join(outdir, 'filepack.bin')
    write_named(out_pack, names, [name_to_blob[n] for n in names], info)

    # --- 5 : copy the rest (never delete anything, just overwrite) --------
    for fn in os.listdir(src_dir):
        sp = os.path.join(src_dir, fn)
        dp = os.path.join(outdir, fn)
        if fn == 'filepack.bin':
            continue
        if os.path.isdir(sp):
            shutil.copytree(sp, dp, dirs_exist_ok=True)
        else:
            shutil.copy2(sp, dp)
    print('[build] %s -> %s' % (src_dir, outdir))
    return dict(written=written, overflow=overflow, missing=missing,
                out=out_pack)


def write_named(path, names, blobs, info):
    count = len(names)
    rec_off = info.get('rec_off', 0x20)
    names_off = rec_off + count * 12
    nt = bytearray()
    noffs = []
    for n in names:
        raw = n.encode('latin1') + b'\x00'
        pad = (-len(raw)) % 4
        noffs.append(names_off + len(nt))
        nt += raw + b'\x00' * pad
    data_off = fpack.align16(names_off + len(nt))
    offs, cur = [], data_off
    for b in blobs:
        offs.append(cur)
        cur = fpack.align16(cur + len(b))
    total = cur
    with open(path, 'wb') as f:
        f.write(struct.pack('<IIIIIIII', total, count, data_off,
                            info.get('unk0c', 4), info.get('unk10', 16),
                            rec_off, names_off, data_off))
        for i in range(count):
            f.write(struct.pack('<III', noffs[i], offs[i], len(blobs[i])))
        f.write(nt)
        for i, b in enumerate(blobs):
            f.seek(offs[i])
            f.write(b)
        f.truncate(total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src_dir')
    ap.add_argument('--json', required=True)
    ap.add_argument('--meta', required=True)
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--no-force', action='store_true')
    a = ap.parse_args()
    build(a.src_dir, a.json, a.meta, a.outdir, force=not a.no_force)


if __name__ == '__main__':
    main()
