#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fpack.py — "No-FilePlus" (Dm-No-FilePlusD) archive tool  —  The Beautiful World / GSX engine
=============================================================================================
Handles both container flavours used by this engine:

  FORMAT A — *named* pack  (filepack.bin, the top-level archive)
      u32  total_size          total file size; the first 4 bytes of the file itself
      u32  count               number of entries
      u32  meta_size           size of header+records+names  (== data start offset)
      u32  unk0c               always 4
      u32  unk10               always 16
      u32  rec_off             record table offset (always 0x20)
      u32  names_off           name table offset (== 0x20 + count*12)
      u32  data_off            first data block offset (== meta_size)
      record[count]  { u32 name_off; u32 data_off; u32 size }     12 bytes each
      name table      NUL-terminated names, each padded so (len+1) rounds up to 4
      data blocks     raw file bytes, next block 16-byte aligned

  FORMAT B — *index* pack  (script2d.bin / title_menu.bin / vpaku.bin / se_com.bin …)
      u32  total_size
      u32  count
      u32  rel[count]           absolute offsets of each entry's data
      data blocks               entry i = rel[i] .. rel[i+1]   (16-byte aligned start)

  NOTE   a 16-byte entry equal to b"Dm-No-FilePlusD\\x00" is the engine's
         "empty / placeholder" marker and is preserved verbatim.

Usage (drag & drop a folder/file onto this script also works):
  python fpack.py list   <archive>
  python fpack.py unpack <archive> <outdir>
  python fpack.py pack   <indir>   <archive>
  python fpack.py verify <archive>            # unpack -> pack -> bit-compare
"""
import os, sys, struct, json, hashlib, shutil, tempfile

SIG = b'Dm-No-FilePlusD\x00'
MAGIC = 'Dm-No-FilePlusD'
FORMAT_A = 'named'
FORMAT_B = 'index'


# ----------------------------------------------------------------------------- helpers
def detect_format(data):
    """Return FORMAT_A / FORMAT_B, or None."""
    if len(data) < 16:
        return None
    if data[:8] == b'Dm-No-File':          # a raw "empty pack" marker
        return 'empty'
    size, count = struct.unpack_from('<II', data, 0)
    if size != len(data):
        return None
    if count == 0:
        return FORMAT_B if size == 8 else None
    # Format A: [0x14] must be the record-table offset (0x20) and
    #           [0x18] == 0x20 + count*12
    rec_off, names_off = struct.unpack_from('<II', data, 0x14)
    if rec_off == 0x20 and names_off == 0x20 + count * 12 and names_off <= size:
        # cross-check: every record must point at a NUL-terminated ASCII name
        ok = True
        for i in range(min(count, 8)):
            no, do, sz = struct.unpack_from('<III', data, 0x20 + i * 12)
            if not (names_off <= no < size) or not (0 <= do <= size) or do + sz > size:
                ok = False
                break
            if b'\x00' not in data[no:no + 64]:
                ok = False
                break
        if ok:
            return FORMAT_A
    # Format B: rel[] must be monotonic and start at align16(8+count*4)
    need = 8 + count * 4
    rel = struct.unpack_from('<%dI' % count, data, 8)
    if rel[0] != (need + 15) // 16 * 16 and rel[0] < need:
        return None
    if any(rel[i] > rel[i + 1] for i in range(count - 1)):
        return None
    if rel[-1] > size:
        return None
    return FORMAT_B


def align16(x):
    return (x + 15) // 16 * 16


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ----------------------------------------------------------------------------- readers
def read_named(data):
    size, count, meta, u1, u2, rec_off, names_off, data_off = struct.unpack_from('<8I', data, 0)
    entries = []
    for i in range(count):
        no, do, sz = struct.unpack_from('<III', data, rec_off + i * 12)
        name_end = data.index(b'\x00', no)
        name = data[no:name_end].decode('latin1')
        entries.append({'name': name, 'name_off': no, 'off': do, 'size': sz})
    return {'format': FORMAT_A, 'size': size, 'count': count, 'meta_size': meta,
            'unk0c': u1, 'unk10': u2, 'rec_off': rec_off, 'names_off': names_off,
            'data_off': data_off, 'entries': entries}


def read_index(data):
    size, count = struct.unpack_from('<II', data, 0)
    rel = list(struct.unpack_from('<%dI' % count, data, 8))
    entries = []
    for i in range(count):
        off = rel[i]
        end = rel[i + 1] if i + 1 < count else size
        entries.append({'name': '%05d.bin' % i, 'index': i, 'off': off, 'size': end - off})
    return {'format': FORMAT_B, 'size': size, 'count': count, 'entries': entries}


def parse_archive(path):
    data = open(path, 'rb').read()
    fmt = detect_format(data)
    if fmt == 'empty':
        return {'format': 'empty', 'size': len(data), 'count': 0, 'entries': []}
    if fmt == FORMAT_A:
        return read_named(data)
    if fmt == FORMAT_B:
        return read_index(data)
    raise ValueError('unknown archive format: %s' % path)


# ----------------------------------------------------------------------------- unpack
def unpack(archive, outdir, verbose=True):
    info = parse_archive(archive)
    data = open(archive, 'rb').read()
    os.makedirs(outdir, exist_ok=True)
    order = []
    for i, e in enumerate(info['entries']):
        rel = e['name'].lstrip('/').replace('/', os.sep)
        if rel.strip() in ('', '.'):
            rel = '%05d.bin' % i
        dst = os.path.join(outdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'wb').write(data[e['off']:e['off'] + e['size']])
        order.append((i, e['name']))
    man = {'tool': 'fpack', 'format': info['format'], 'archive': os.path.basename(archive),
           'orig_size': info['size'], 'count': info['count'],
           'header': {k: info[k] for k in ('meta_size', 'unk0c', 'unk10', 'rec_off',
                                           'names_off', 'data_off') if k in info},
           'entries': [{'index': i, 'name': e['name'], 'size': e['size']}
                       for i, e in enumerate(info['entries'])]}
    open(os.path.join(outdir, '_manifest.json'), 'w', encoding='utf-8').write(
        json.dumps(man, ensure_ascii=False, indent=1))
    with open(os.path.join(outdir, '_order.txt'), 'w', encoding='utf-8') as f:
        f.write('# index\tname\n')
        for i, n in order:
            f.write('%d\t%s\n' % (i, n))
    if verbose:
        print('[unpack] %s -> %s  (%d entries, %s)' % (archive, outdir, info['count'], info['format']))
    return info


# ----------------------------------------------------------------------------- pack
def pack(indir, archive, verbose=True):
    man = json.load(open(os.path.join(indir, '_manifest.json'), encoding='utf-8'))
    fmt = man['format']
    blobs = []
    for e in man['entries']:
        rel = e['name'].lstrip('/').replace('/', os.sep)
        p = os.path.join(indir, rel)
        blobs.append(open(p, 'rb').read())
    if fmt == FORMAT_B:
        out = build_index(blobs)
    elif fmt == FORMAT_A:
        out = build_named([e['name'] for e in man['entries']], blobs, man['header'])
    else:
        raise ValueError('cannot pack format %s' % fmt)
    open(archive, 'wb').write(out)
    if verbose:
        print('[pack] %s -> %s  (%d entries, %d bytes)' % (indir, archive, len(blobs), len(out)))
    return out


def build_index(blobs):
    count = len(blobs)
    head = 8 + count * 4
    start = align16(head)
    offs, cur = [], start
    for b in blobs:
        offs.append(cur)
        cur = align16(cur + len(b))
    total = cur
    out = bytearray(total)
    struct.pack_into('<II', out, 0, total, count)
    for i, o in enumerate(offs):
        struct.pack_into('<I', out, 8 + i * 4, o)
    for i, b in enumerate(blobs):
        out[offs[i]:offs[i] + len(b)] = b
    return bytes(out)


def build_named(names, blobs, header):
    count = len(names)
    rec_off = header.get('rec_off', 0x20)
    names_off = rec_off + count * 12
    # name table: each name + NUL, padded to 4
    nt = bytearray()
    noffs = []
    for n in names:
        raw = n.encode('latin1') + b'\x00'
        pad = (-len(raw)) % 4
        noffs.append(names_off + len(nt))
        nt += raw + b'\x00' * pad
    data_off = align16(names_off + len(nt))      # data area starts 16-byte aligned
    offs, cur = [], data_off
    for b in blobs:
        offs.append(cur)
        cur = align16(cur + len(b))
    total = cur if blobs else data_off
    out = bytearray(total)
    struct.pack_into('<IIIIIIII', out, 0, total, count, data_off,
                     header.get('unk0c', 4), header.get('unk10', 16),
                     rec_off, names_off, data_off)
    for i in range(count):
        struct.pack_into('<III', out, rec_off + i * 12, noffs[i], offs[i], len(blobs[i]))
    out[names_off:names_off + len(nt)] = nt
    for i, b in enumerate(blobs):
        out[offs[i]:offs[i] + len(b)] = b
    return bytes(out)


# ----------------------------------------------------------------------------- verify
def verify(archive):
    info = parse_archive(archive)
    data = open(archive, 'rb').read()
    tmp = tempfile.mkdtemp(prefix='fpack_verify_')
    try:
        unpack(archive, tmp, verbose=False)
        if info['format'] == 'empty':
            print('[verify] %s : empty marker, nothing to do' % archive)
            return True
        rebuild = pack(tmp, os.path.join(tmp, '_rebuilt.bin'), verbose=False)
        ok = rebuild == data
        print('[verify] %s : %s  (%d bytes)' % (archive, 'BIT-PERFECT' if ok else 'MISMATCH', len(data)))
        if not ok:
            for i in range(min(len(data), len(rebuild))):
                if data[i] != rebuild[i]:
                    print('        first difference at 0x%x (orig %02x vs new %02x)'
                          % (i, data[i], rebuild[i]))
                    break
            print('        sizes orig=%d new=%d' % (len(data), len(rebuild)))
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- cli
def usage():
    print(__doc__)
    sys.exit(1)


def main():
    argv = sys.argv[1:]
    if not argv:
        usage()
    cmd = argv[0]
    if cmd == 'list':
        info = parse_archive(argv[1])
        print('%s : %s, %d entries, %d bytes' % (argv[1], info['format'], info['count'], info['size']))
        for i, e in enumerate(info['entries'][:60]):
            print('  %4d  off=%-10d size=%-10d %s' % (i, e['off'], e['size'], e['name']))
        if info['count'] > 60:
            print('  ... (%d more)' % (info['count'] - 60))
    elif cmd == 'unpack':
        unpack(argv[1], argv[2])
    elif cmd == 'pack':
        pack(argv[1], argv[2])
    elif cmd == 'verify':
        for a in argv[1:]:
            verify(a)
    elif cmd == 'info':
        print(json.dumps({k: v for k, v in parse_archive(argv[1]).items() if k != 'entries'},
                         ensure_ascii=False, indent=1))
    else:
        usage()


if __name__ == '__main__':
    main()
