#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pak_tool.py —— OCArchive v3 (Nitroplus / "OC" 引擎 .pak) 封包工具
================================================================================
支持：list / unpack / pack / verify / extract

格式（全部字段经逆向 + 35000+ 条样本交叉验证，见 report/引擎分析报告.md）

  ┌────────── 文件头 ──────────────────────────────────────────────┐
  │ 0x000  u32            版本号 = 3                                │
  │ 0x004  char[0x100]    包名（CP932，0x00 填充）"Hello_worldDVD"   │
  │ 0x104  u32            加密类型 = 0xC8（定为 K0）                 │
  │ 0x108  u32            hash0x89(包名) ^ 索引未压缩长度            │
  │ 0x10C  u32            hash0x89(包名) ^ 条目数                    │
  │ 0x110  u32            K0 ^ 索引压缩后长度                        │
  │ 0x114  ...            索引区：zlib(索引)                         │
  ├────────── 数据区（紧随索引）────────────────────────────────────┤
  │ 每条目：见下                                                    │
  ├────────── 尾部 ────────────────────────────────────────────────┤
  │ 固定 0x100000 字节尾块（内容不定，原样保留）                     │
  └─────────────────────────────────────────────────────────────────┘

  索引 = 连续记录，每条：
      [u32 name_len][name bytes(cp932)][ u32 f0 ][ u32 f1 ][ u32 f2 ][ u32 f3 ][ u32 f4 ]
      其中 f_i 是加密值：明文 = f_i ^ key，key = hash0x89(name)
      明文五元组 = (数据区偏移, 原始大小, 本条索引结束偏移, 存储方式, 压缩大小)
          存储方式 1 = zlib 压缩；0 = 原样存储

  hash0x89(s)：h = 0; for c in s: h = (h * 0x89 + signed(c)) & 0xFFFFFFFF

  条目数据：
      存储方式 = 1：zlib 流，长度 = 压缩大小（无混淆）
      存储方式 = 0：原样，长度 = 原始大小，**前 16 字节 = 明文 XOR (key 的 4 字节 LE 重复×4)**

用法
----
  python pak_tool.py list    <pak> [--filter 关键字]
  python pak_tool.py extract <pak> <包内路径> [-o 输出文件]
  python pak_tool.py unpack  <pak> [-o 目录] [--only 关键字] [--dump-raw]
  python pak_tool.py pack    <pak> <目录> [-o 新pak] [--reserve 预留字节]
  python pak_tool.py verify  <pak> [--work 临时目录]

  拖放：把 .pak 拖到本脚本上 = unpack 到同名目录。
"""
import argparse
import hashlib
import json
import os
import struct
import sys
import zlib

VERSION = 3
NAME_FIELD = 0x100
HDR_OFF = 0x104
INDEX_OFF = 0x114
K0_DEFAULT = 0xC8
TRAILER_SIZE = 0x100000


# ----------------------------------------------------------------------------- 基础
def hash0x89(s: bytes) -> int:
    """引擎内的字符串哈希（system.dll:0x406190 反编译所得）。"""
    h = 0
    for b in s:
        h = (h * 0x89 + (b - 256 if b > 127 else b)) & 0xFFFFFFFF
    return h


def ks16(key: int) -> bytes:
    """条目头部 16 字节混淆用的密钥流（4 字节 LE 重复）。"""
    return struct.pack('<I', key) * 4


def decode_name(raw: bytes) -> str:
    return raw.decode('cp932', 'replace')


def encode_name(name: str) -> bytes:
    return name.encode('cp932')


# ----------------------------------------------------------------------------- 解析
class Pak:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        b = self.data
        if len(b) < INDEX_OFF:
            raise ValueError('文件太小，不是 OCArchive')
        self.ver, = struct.unpack_from('<I', b, 0)
        if self.ver not in (2, 3):
            raise ValueError(f'不支持的版本 {self.ver}（本工具针对 v3/v2）')
        self.name_raw = b[4:4 + NAME_FIELD]
        self.name = self.name_raw.rstrip(b'\x00')
        self.hdr = list(struct.unpack_from('<4I', b, HDR_OFF))
        self.toc_raw, self.toc_csize = self._inflate_index()
        self.toc_usize = len(self.toc_raw)
        self.region_start = INDEX_OFF + self.toc_csize
        self.entries = self._parse_index()
        self.trailer = b[len(b) - TRAILER_SIZE:] if len(b) > TRAILER_SIZE else b''

    def _inflate_index(self):
        d = zlib.decompressobj()
        out = d.decompress(self.data[INDEX_OFF:])
        used = len(self.data[INDEX_OFF:]) - len(d.unused_data)
        return out, used

    def _parse_index(self):
        toc, es, p = self.toc_raw, [], 0
        while p + 4 <= len(toc):
            nl, = struct.unpack_from('<I', toc, p)
            if not (0 < nl < 1024) or p + 4 + nl + 20 > len(toc):
                break
            nb = toc[p + 4:p + 4 + nl]
            raw = struct.unpack_from('<5I', toc, p + 4 + nl)
            k = hash0x89(nb)
            es.append(dict(index=len(es), name_raw=nb, name=decode_name(nb), key=k,
                           offset=raw[0] ^ k, usize=raw[1] ^ k, index_end=raw[2] ^ k,
                           method=raw[3] ^ k, csize=raw[4] ^ k,
                           meta_raw=raw))
            p += 4 + nl + 20
        self.index_consumed = p
        return es

    # ---- 单条目读取
    def stored_size(self, e):
        return e['csize'] if e['method'] == 1 else e['usize']

    def read_entry(self, e) -> bytes:
        st = self.region_start + e['offset']
        if e['method'] == 1:
            raw = self.data[st:st + e['csize']]
            out = zlib.decompress(raw)
            if len(out) != e['usize']:
                raise ValueError(f"{e['name']}: 解压大小 {len(out)} != 索引 {e['usize']}")
            return out
        raw = self.data[st:st + e['usize']]
        n = min(16, len(raw))
        head = bytes(x ^ y for x, y in zip(raw[:n], ks16(e['key'])[:n]))
        return head + raw[n:]

    def read_entry_raw(self, e) -> bytes:
        """磁盘上的原始存储字节（未解混淆/未解压）。"""
        st = self.region_start + e['offset']
        return self.data[st:st + self.stored_size(e)]

    # ---- 自检
    def selfcheck(self):
        msgs = []
        need = 0
        for e in self.entries:
            if e['offset'] != need:
                msgs.append(f"#{e['index']} {e['name']} 偏移断链: {e['offset']} != {need}")
            need += self.stored_size(e)
        end = self.region_start + need
        tail = len(self.data) - end
        hn = hash0x89(self.name)
        if self.hdr[1] ^ hn != self.toc_usize:
            msgs.append(f'头部字段1 校验失败: {self.hdr[1]^hn} != {self.toc_usize}')
        if self.hdr[2] ^ hn != len(self.entries):
            msgs.append(f'头部字段2 校验失败: {self.hdr[2]^hn} != {len(self.entries)}')
        if self.hdr[3] ^ self.hdr[0] != self.toc_csize:
            msgs.append(f'头部字段3 校验失败: {self.hdr[3]^self.hdr[0]} != {self.toc_csize}')
        if tail != TRAILER_SIZE:
            msgs.append(f'尾部长度 {tail} != {TRAILER_SIZE}（0x100000）')
        return msgs


# ----------------------------------------------------------------------------- 打包
def build(pak: Pak, contents, out_path, reserve=0):
    """contents: {name: bytes}（按需覆盖，未给出的沿用原文件内容）
    返回写入的字节数。"""
    entries = []
    for e in pak.entries:
        data = contents.get(e['name'])
        if data is None:
            data = pak.read_entry(e)
        entries.append((e, data))

    key_pak = hash0x89(pak.name)
    index = bytearray()
    body = bytearray()
    for e, data in entries:
        k = e['key']
        if e['method'] == 1:
            blob = zlib.compress(data, 6)
            csize = len(blob)
        else:
            blob = bytearray(data)
            n = min(16, len(blob))
            blob[:n] = bytes(x ^ y for x, y in zip(blob[:n], ks16(k)[:n]))
            blob = bytes(blob)
            csize = 0
        offset = len(body)
        index += struct.pack('<I', len(e['name_raw'])) + e['name_raw']
        index_end = len(index) + 20
        index += struct.pack('<5I', offset ^ k, len(data) ^ k, index_end ^ k,
                             e['method'] ^ k, csize ^ k)
        body += blob
        if reserve:
            body += b'\x00' * reserve

    blob_index = zlib.compress(bytes(index), 6)
    out = bytearray()
    out += struct.pack('<I', VERSION)
    out += pak.name_raw[:NAME_FIELD].ljust(NAME_FIELD, b'\x00')
    out += struct.pack('<4I', pak.hdr[0], key_pak ^ len(index), key_pak ^ len(entries),
                       pak.hdr[0] ^ len(blob_index))
    out += blob_index
    out += body
    out += pak.trailer

    with open(out_path, 'wb') as f:
        f.write(out)
    return len(out)


# ----------------------------------------------------------------------------- 命令
def cmd_list(pak, args):
    msgs = pak.selfcheck()
    print(f'包名: {pak.name.decode("cp932", "replace")}  版本: {pak.ver}  条目: {len(pak.entries)}')
    print(f'索引: usize={pak.toc_usize} csize={pak.toc_csize}  数据区起止: 0x{pak.region_start:X}..'
          f'0x{pak.region_start + sum(pak.stored_size(e) for e in pak.entries):X}  文件: {len(pak.data)} 字节')
    if msgs:
        print('自检异常:' if not msgs else '自检异常:')
        for m in msgs[:10]:
            print('   ' + m)
    else:
        print('自检: 通过（偏移链 / 头部字段 / 尾部长度）')
    kw = args.filter
    print(f'{"#":>6} {"方式":<6} {"原始":>10} {"存储":>10} {"偏移":>10}  名称')
    n = 0
    for e in pak.entries:
        if kw and kw not in e['name']:
            continue
        m = 'zlib' if e['method'] == 1 else 'raw'
        print(f'{e["index"]:>6} {m:<6} {e["usize"]:>10} {pak.stored_size(e):>10} {e["offset"]:>10}  {e["name"]}')
        n += 1
        if args.limit and n >= args.limit:
            print(f'...（--limit {args.limit}）')
            break


def cmd_extract(pak, args):
    target = args.name.replace('/', '\\')
    for e in pak.entries:
        if e['name'] == target or e['name'].lower() == target.lower():
            data = pak.read_entry(e)
            outp = args.output or os.path.basename(e['name'].replace('\\', '_'))
            with open(outp, 'wb') as f:
                f.write(data)
            print(f'[OK] {e["name"]} -> {outp}  ({len(data)} 字节)')
            return
    print(f'[错误] 包内未找到: {args.name}')
    sys.exit(1)


def cmd_unpack(pak, args):
    outdir = args.output or os.path.splitext(pak.path)[0] + '_unpack'
    os.makedirs(outdir, exist_ok=True)
    order, man = [], []
    n = 0
    for e in pak.entries:
        if args.only and args.only not in e['name']:
            order.append({'index': e['index'], 'name': e['name'], 'skipped': True})
            continue
        rel = e['name'].replace('\\', '/')
        dest = os.path.join(outdir, rel)
        os.makedirs(os.path.dirname(dest) or outdir, exist_ok=True)
        data = pak.read_entry(e)
        with open(dest, 'wb') as f:
            f.write(data)
        rec = {'index': e['index'], 'name': e['name'], 'method': e['method'],
               'usize': e['usize'], 'csize': e['csize'], 'offset': e['offset'],
               'key': f'{e["key"]:08X}', 'index_end': e['index_end'],
               'meta_raw': [f'{v:08X}' for v in e['meta_raw']],
               'sha1': hashlib.sha1(data).hexdigest()}
        if args.dump_raw:
            rawp = dest + '.raw'
            with open(rawp, 'wb') as f:
                f.write(pak.read_entry_raw(e))
            rec['raw_file'] = os.path.basename(rawp)
        order.append({'index': e['index'], 'name': e['name']})
        man.append(rec)
        n += 1
    with open(os.path.join(outdir, '_order.txt'), 'w', encoding='utf-8') as f:
        f.write('# index\tname\t(顺序即包内顺序, 原样保留)\n')
        for o in order:
            f.write(f'{o["index"]}\t{o["name"]}{"  #SKIPPED" if o.get("skipped") else ""}\n')
    manifest = {
        'tool': 'pak_tool',
        'format': 'OCArchive v3 (Nitroplus / OC engine .pak)',
        'note': ('索引字段 = 明文 XOR key, key = hash0x89(条目名); '
                 '存储方式1=zlib(level6 可逐字节一致), 0=原样存储且前16字节 XOR key; '
                 '尾部固定 0x100000 字节原样保留。'),
        'source_pak': os.path.basename(pak.path),
        'orig_file_size': len(pak.data),
        'archive_name': pak.name.decode('cp932', 'replace'),
        'version': pak.ver,
        'header_hex': ' '.join(f'{v:08X}' for v in pak.hdr),
        'k0': pak.hdr[0],
        'toc_usize': pak.toc_usize,
        'toc_csize': pak.toc_csize,
        'region_start': pak.region_start,
        'entry_count': len(pak.entries),
        'trailer_sha1': hashlib.sha1(pak.trailer).hexdigest(),
        'hash0x89_pakname': f'{hash0x89(pak.name):08X}',
        'entries': man,
    }
    with open(os.path.join(outdir, '_pak_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f'[OK] 解包 {n} 个文件 -> {outdir}')
    print(f'     _order.txt / _pak_manifest.json 已生成')


def cmd_pack(pak, args):
    indir = args.dir
    contents = {}
    missing = []
    for e in pak.entries:
        rel = e['name'].replace('\\', '/')
        p = os.path.join(indir, rel)
        if os.path.exists(p):
            with open(p, 'rb') as f:
                contents[e['name']] = f.read()
        else:
            missing.append(e['name'])
    outp = args.output or (os.path.splitext(pak.path)[0] + '.new.pak')
    size = build(pak, contents, outp, reserve=args.reserve)
    print(f'[OK] 重建 -> {outp}  ({size} 字节)')
    print(f'     目录内命中 {len(contents)}/{len(pak.entries)} 条；缺失 {len(missing)} 条（沿用原内容）')
    for m in missing[:10]:
        print(f'       缺失: {m}')


def cmd_verify(pak, args):
    if args.mem:
        # 纯内存往返（不改动磁盘，适合 cg/voice 这类 GB 级包）
        outp = os.path.join(os.path.dirname(os.path.abspath(pak.path)) or '.', '_mem_rebuild.tmp')
        size = build(pak, {}, outp)
        a = open(pak.path, 'rb').read()
        b = open(outp, 'rb').read()
        os.remove(outp)
        if a == b:
            print(f'[OK] bit-perfect（内存往返）！md5 = {hashlib.md5(a).hexdigest()}  {size} 字节')
        else:
            print(f'[FAIL] 不一致  原始 {len(a)} / 重建 {len(b)}')
            for i in range(min(len(a), len(b))):
                if a[i] != b[i]:
                    print(f'     首个差异 @ 0x{i:X}: {a[i]:02X} != {b[i]:02X}')
                    break
            sys.exit(1)
        return
    import tempfile
    workdir = args.work or os.path.join(os.path.dirname(os.path.abspath(pak.path)), '_verify_tmp')
    os.makedirs(workdir, exist_ok=True)
    print(f'[1/3] 解包 -> {workdir}')
    ns = argparse.Namespace(output=workdir, only=None, dump_raw=False)
    cmd_unpack(pak, ns)
    print('[2/3] 重打包（不解压即用 _pak_manifest.json 顺序）')
    outp = os.path.join(workdir, '_rebuild.pak')
    contents = {}
    for e in pak.entries:
        p = os.path.join(workdir, e['name'].replace('\\', '/'))
        if os.path.exists(p):
            with open(p, 'rb') as f:
                contents[e['name']] = f.read()
    build(pak, contents, outp, reserve=args.reserve)
    print('[3/3] 逐字节比对')
    a = open(pak.path, 'rb').read()
    b = open(outp, 'rb').read()
    if a == b:
        print(f'[OK] bit-perfect！md5 = {hashlib.md5(a).hexdigest()}')
    else:
        print(f'[FAIL] 不一致  原始 {len(a)} 字节 / 重建 {len(b)} 字节')
        n = min(len(a), len(b))
        for i in range(n):
            if a[i] != b[i]:
                print(f'     首个差异 @ 0x{i:X}: {a[i]:02X} != {b[i]:02X}')
                break
        else:
            print('     前缀一致，长度不同')
        sys.exit(1)


# ----------------------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('cmd', nargs='?', help='list/unpack/pack/verify/extract')
    ap.add_argument('pak', nargs='?', help='.pak 文件')
    ap.add_argument('rest', nargs='*', help='pack: 目录; extract: 包内路径')
    ap.add_argument('-o', '--output')
    ap.add_argument('--dir')
    ap.add_argument('--only')
    ap.add_argument('--filter')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--reserve', type=int, default=0)
    ap.add_argument('--work')
    ap.add_argument('--dump-raw', action='store_true')
    ap.add_argument('--mem', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    if len(sys.argv) == 1 or '-h' in sys.argv or '--help' in sys.argv or sys.argv[1] in ('help', '-?'):
        print(__doc__)
        return
    # 拖放：只有一个 .pak 参数
    if len(sys.argv) == 2 and sys.argv[1].lower().endswith('.pak'):
        args = argparse.Namespace(cmd='unpack', pak=sys.argv[1], rest=[], output=None,
                                  dir=None, only=None, filter=None, limit=None,
                                  reserve=0, work=None, dump_raw=False)
    else:
        args = ap.parse_args()
        if args.rest and args.cmd == 'pack' and not args.dir:
            args.dir = args.rest[0]
        if args.rest and args.cmd == 'extract' and not hasattr(args, 'name'):
            args.name = args.rest[0]
    if not args.cmd or not args.pak:
        print(__doc__)
        return
    pak = Pak(args.pak)
    {'list': cmd_list, 'unpack': cmd_unpack, 'pack': cmd_pack,
     'verify': cmd_verify, 'extract': cmd_extract}[args.cmd](pak, args)


if __name__ == '__main__':
    main()
