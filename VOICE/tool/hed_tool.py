"""LOVELESS 归档工具 (data.hed / data.dat)

格式（由 Game.exe FUN_00402120 逆向确认）:
  data.hed 明文布局:
    [0x00..0x0E] 15 bytes : data.dat 文件名 (NUL 填充)
    [0x0F..0x10] u16 LE   : 条目数 count（条目总数 = count + 1）
    [0x11..]     count+1  : 每条 20 字节
                            +0x00 name[12]  (NUL/空格填充的文件名)
                            +0x0C u32 A     (分组/辅助字段，保留原值)
                            +0x10 u32 B     (该文件在 data.dat 中的偏移)
    条目按 B 升序；文件 size = 下一条的 B - 本条 B；最后一条是哨兵 'end'
    （其 B = data.dat 总长度）。

  data.hed 整体用「三重滚动 XOR」加密：三个以 NUL 结尾的密钥各自独立循环
    key = RIC / VOICE / SIDE-B        (Game.exe .data 0x4520b4 / 0x4520b8 / 0x4520c0)
    plain[i] = cipher[i] ^ keyA[i%3] ^ keyB[i%5] ^ keyC[i%6]

  data.dat 是各文件原始字节的顺序拼接，未加密、未压缩。

用法:
  python hed_tool.py unpack <data.hed> <data.dat> <outdir>
  python hed_tool.py pack   <srcdir> <out.hed> <out.dat> [--manifest manifest.json]
  python hed_tool.py list   <data.hed>
  python hed_tool.py verify <data.hed> <data.dat> <srcdir>
"""
import json
import os
import struct
import sys

KEY_A = b'RIC'
KEY_B = b'VOICE'
KEY_C = b'SIDE-B'

ENTRY_SIZE = 20
NAME_SIZE = 12
HDR_SIZE = 0x11          # 15 + 2
SENTINEL = 'end'


def _xor(buf):
    return bytes(buf[i] ^ KEY_A[i % 3] ^ KEY_B[i % 5] ^ KEY_C[i % 6]
                 for i in range(len(buf)))


def load_hed(path):
    raw = open(path, 'rb').read()
    dec = _xor(raw)
    dat_name = dec[0:15].split(b'\x00')[0].decode('cp932')
    count = struct.unpack_from('<H', dec, 0x0F)[0]
    n = count + 1
    exp = HDR_SIZE + n * ENTRY_SIZE
    if exp != len(dec):
        raise ValueError('hed size mismatch: expected %d, got %d' % (exp, len(dec)))
    entries = []
    for i in range(n):
        o = HDR_SIZE + i * ENTRY_SIZE
        raw = dec[o:o + NAME_SIZE]
        name = raw.split(b'\x00')[0].decode('cp932')
        a, b = struct.unpack_from('<II', dec, o + NAME_SIZE)
        # name_raw is kept verbatim: the original fills the unused tail of the
        # 12-byte field with non-NUL padding (observed 0x64), so we must not
        # "normalise" it if we want a bit-identical rebuild.
        entries.append({'name': name, 'name_raw': raw.hex(), 'a': a, 'offset': b})
    for i, e in enumerate(entries):
        e['size'] = (entries[i + 1]['offset'] - e['offset']) if i + 1 < n else 0
    return dat_name, entries


def build_hed(dat_name, entries):
    """entries: list of dict(name, a, offset) ; the last one is the sentinel."""
    n = len(entries)
    buf = bytearray()
    nm = dat_name.encode('cp932')[:15]
    buf += nm + b'\x00' * (15 - len(nm))
    buf += struct.pack('<H', n - 1)
    for e in entries:
        raw = bytes.fromhex(e['name_raw']) if e.get('name_raw') else None
        if raw is None:
            raw = e['name'].encode('cp932')
            raw = raw + b'\x00' * (NAME_SIZE - len(raw))
        if len(raw) != NAME_SIZE:
            raise ValueError('bad name_raw length for %r' % e['name'])
        buf += raw
        buf += struct.pack('<II', e['a'] & 0xFFFFFFFF, e['offset'] & 0xFFFFFFFF)
    return _xor(buf)


def cmd_unpack(hed, dat, outdir):
    dat_name, entries = load_hed(hed)
    print('data file name in hed : %s' % dat_name)
    print('entries               : %d' % len(entries))
    data = open(dat, 'rb').read()
    real = [e for e in entries if e['name'] != SENTINEL]
    os.makedirs(outdir, exist_ok=True)
    for e in real:
        blob = data[e['offset']:e['offset'] + e['size']]
        p = os.path.join(outdir, e['name'])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(blob)
    # manifest keeps every field so pack() is loss-free
    man = {'dat_name': dat_name,
           'entries': [{'name': e['name'], 'name_raw': e['name_raw'],
                        'a': e['a'], 'size': e['size']} for e in entries]}
    with open(os.path.join(outdir, '_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print('unpacked %d files -> %s' % (len(real), outdir))
    return real


def cmd_pack(srcdir, out_hed, out_dat, manifest=None, extra_dirs=()):
    """Pack a directory tree back into DATA.HED / DATA.DAT.

    A file is looked up in srcdir first, then in each of extra_dirs. That makes
    it possible to keep only the modified files (e.g. the 859 .ovl scripts) in
    srcdir and read everything else from the untouched unpack tree.
    """
    man_path = manifest or os.path.join(srcdir, '_manifest.json')
    man = json.load(open(man_path, encoding='utf-8'))
    dat_name = man['dat_name']
    ents = man['entries']
    blob = bytearray()
    out_entries = []
    for e in ents:
        if e['name'] == SENTINEL:
            out_entries.append({'name': SENTINEL, 'name_raw': e['name_raw'],
                                'a': e['a'], 'offset': len(blob)})
            continue
        p = os.path.join(srcdir, e['name'])
        if not os.path.exists(p):
            for d in extra_dirs:
                q = os.path.join(d, e['name'])
                if os.path.exists(q):
                    p = q
                    break
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        data = open(p, 'rb').read()
        out_entries.append({'name': e['name'], 'name_raw': e['name_raw'],
                            'a': e['a'], 'offset': len(blob)})
        blob += data
    hed = build_hed(dat_name, out_entries)
    open(out_hed, 'wb').write(hed)
    open(out_dat, 'wb').write(bytes(blob))
    print('packed %d files -> %s (%d bytes) + %s' % (len(out_entries) - 1, out_dat, len(blob), out_hed))
    return out_entries


def cmd_list(hed):
    dat_name, entries = load_hed(hed)
    print('%-16s %10s %10s' % ('name', 'offset', 'size'))
    for e in entries:
        print('%-16s %10d %10d' % (e['name'], e['offset'], e['size']))


def cmd_verify(hed, dat, srcdir):
    _, entries = load_hed(hed)
    data = open(dat, 'rb').read()
    bad = 0
    for e in entries:
        if e['name'] == SENTINEL:
            continue
        p = os.path.join(srcdir, e['name'])
        disk = open(p, 'rb').read()
        packed = data[e['offset']:e['offset'] + e['size']]
        if disk != packed:
            print('MISMATCH', e['name'], len(disk), len(packed))
            bad += 1
    print('verify: %d mismatches / %d files' % (bad, len(entries) - 1))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == 'unpack':
        cmd_unpack(a[1], a[2], a[3])
    elif a[0] == 'pack':
        cmd_pack(a[1], a[2], a[3], a[4] if len(a) > 4 else None)
    elif a[0] == 'list':
        cmd_list(a[1])
    elif a[0] == 'verify':
        cmd_verify(a[1], a[2], a[3])
    else:
        print(__doc__)
