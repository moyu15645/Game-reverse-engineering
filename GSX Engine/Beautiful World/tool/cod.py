# -*- coding: utf-8 -*-
"""Low-level primitives for The Beautiful World / GSX engine.

* No-FilePlus archive reading (delegates to fpack.py)
* CoD.cpt stream-cipher (differential substitution) decrypt/encrypt
* CoD.cpt container parse / rebuild
"""
import struct, sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fpack

MAGIC = 0x01050400


# --------------------------------------------------------------- crypto table
def find_table_va(exe_path):
    """Locate the 256-byte substitution table by finding the indexing instruction
    `MOV AL, byte ptr [EAX + table]` (8A 80 xx xx xx 00) inside the exe."""
    d = open(exe_path, 'rb').read()
    out = []
    for m in re.finditer(rb'\x8a\x80', d):
        addr = struct.unpack_from('<I', d, m.start() + 2)[0]
        if 0x4e0000 <= addr < 0x1600000:
            out.append(addr)
    # prefer the one referenced from the small de-obfuscation loop (earliest)
    if not out:
        raise RuntimeError('crypto table not found in %s' % exe_path)
    return out[0]


def pe_sections(exe_path):
    d = open(exe_path, 'rb').read()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = struct.unpack_from('<H', d, pe + 20)[0]
    imgbase = struct.unpack_from('<I', d, pe + 24 + 28)[0]
    secs = []
    for i in range(nsec):
        o = pe + 24 + opt + i * 40
        name = d[o:o + 8].rstrip(b'\x00').decode('latin1')
        vs, va, rs, ra = struct.unpack_from('<IIII', d, o + 8)
        secs.append((name, imgbase + va, rs, ra))
    return d, imgbase, secs


def v2f(exe_path, va):
    d, base, secs = pe_sections(exe_path)
    for name, sva, rs, ra in secs:
        if sva <= va < sva + rs:
            return ra + (va - sva)
    raise ValueError('VA 0x%x not mapped' % va)


def load_table(exe_path):
    va = find_table_va(exe_path)
    exe = open(exe_path, 'rb').read()
    fo = v2f(exe_path, va)
    tbl = exe[fo:fo + 256]
    if len(set(tbl)) != 256:
        raise ValueError('table at 0x%x is not a permutation' % va)
    return tbl


def make_inverse(tbl):
    inv = [0] * 256
    for i, b in enumerate(tbl):
        inv[b] = i
    return inv


def decrypt(data, tbl):
    out = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        out[i] = tbl[(b - prev) & 0xFF]
        prev = b
    return bytes(out)


def encrypt(plain, tbl, inv):
    out = bytearray(len(plain))
    prev = 0
    for i, p in enumerate(plain):
        b = (inv[p] + prev) & 0xFF
        out[i] = b
        prev = b
    return bytes(out)


# --------------------------------------------------------------- archive access
def read_pack(path):
    return fpack.parse_archive(path)


def get_file(path, name):
    info = fpack.parse_archive(path)
    data = open(path, 'rb').read()
    for e in info['entries']:
        if e['name'] == name:
            return data[e['off']:e['off'] + e['size']], info
    raise KeyError(name)


# --------------------------------------------------------------- CoD container
HDR = 0x100


def parse_cod(d):
    magic, count = struct.unpack_from('<II', d, 0)
    if magic != MAGIC:
        raise ValueError('bad CoD magic 0x%08x' % magic)
    scripts = []
    base = HDR + count * 0x10
    for i in range(count):
        rel, payload = struct.unpack_from('<II', d, HDR + i * 16)
        blk = base + i * 0x40 + rel
        if blk + 0x40 > len(d):
            raise ValueError('block %d out of range' % i)
        code_words, data_size = struct.unpack_from('<II', d, blk)
        nm = d[blk + 0x10:blk + 0x20].split(b'\x00')[0].decode('latin1')
        scripts.append(dict(idx=i, blk=blk, code_words=code_words, data_size=data_size,
                            name=nm, payload=payload,
                            code_off=blk + 0x40, code_len=code_words * 4,
                            data_off=blk + 0x40 + code_words * 4, data_len=data_size,
                            hdr16a=d[blk + 0x20:blk + 0x30], hdr16b=d[blk + 0x30:blk + 0x40]))
    return magic, count, scripts


def build_cod(count, blocks, hdr_pad=b'\x00' * 0xF8):
    """blocks: list of dict(name=, code=bytes, data=bytes, hdr16a=, hdr16b=)"""
    base = HDR + count * 0x10
    index = bytearray(count * 16)
    body = bytearray()
    cum = 0
    for i, b in enumerate(blocks):
        code = b['code']
        data = b['data']
        words = len(code) // 4
        assert len(code) % 4 == 0
        assert len(b['name']) <= 16
        blk = bytearray(0x40)
        struct.pack_into('<II', blk, 0, words, len(data))
        nm = b['name'].encode('latin1') + b'\x00'
        blk[0x10:0x10 + len(nm)] = nm
        blk[0x20:0x30] = b.get('hdr16a', b'\x00' * 16)
        blk[0x30:0x40] = b.get('hdr16b', b'\x00' * 16)
        struct.pack_into('<II', index, i * 16, cum, len(code) + len(data))
        cum += 0x40 + len(code) + len(data)
        body += blk + code + data
    out = bytearray()
    out += struct.pack('<II', MAGIC, count)
    out += hdr_pad
    out += index
    out += body
    return bytes(out)
