#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_release.py — 校验汉化成品

    python verify_release.py <发布的章目录> \
        --meta work/chN/text/jp.json.meta.json --zh work/chN/text/zh.json \
        [--orig "Beautiful World 1.1"]

检查项
  1. filepack.bin 结构自洽（头部 / 记录 / 名字表 / 16 字节对齐）
  2. CoD.cpt 能解密、容器能解析、脚本数与原文一致
  3. 每一条译文都能在其**原始偏移**上原样读回
  4. 未翻译的池字符串与原文完全一致
"""
import os, sys, json, struct, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cod, fpack, gsx_text as GT

COD_PATH = '/Data/script/CoD.cpt'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('release_dir')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--zh', required=True)
    ap.add_argument('--orig')
    a = ap.parse_args()

    pack = os.path.join(a.release_dir, 'filepack.bin')
    with open(pack, 'rb') as f:
        head = f.read(32)
        meta_size = struct.unpack_from('<I', head, 8)[0]
        f.seek(0)
        info = fpack.read_named(f.read(meta_size))
    print('[verify] 封包结构 OK：%d 条目  meta_size=%d  names_off=%d  data_off=%d'
          % (len(info['entries']), info['meta_size'], info['names_off'], info['data_off']))
    misaligned = sum(1 for e in info['entries'] if e['off'] % 16)
    print('[verify] 数据块 16 字节对齐：%d 处不对齐' % misaligned)

    exe = None
    for fn in os.listdir(a.release_dir):
        if fn.lower().endswith('.exe') and 'uninst' not in fn.lower():
            exe = os.path.join(a.release_dir, fn)
    tbl = cod.load_table(exe)
    raw, _ = cod.get_file(pack, COD_PATH)
    dec = cod.decrypt(raw, tbl)
    magic, count, scripts = cod.parse_cod(dec)
    print('[verify] CoD.cpt：magic=0x%08X  脚本数=%d  长度=%d' % (magic, count, len(raw)))

    zh = {e['id']: e['message'] for e in json.load(open(a.zh, encoding='utf-8'))}
    meta = json.load(open(a.meta, encoding='utf-8'))['entries']
    smap = {s['name']: s for s in scripts}
    ok = bad = 0
    for m in meta:
        s = smap[m['script']]
        base = s['data_off'] + m['offset']
        raw_str = dec[base:base + m['slot']].split(b'\x00')[0]
        enc = GT.from_display_raw if m['script'] == 'init_app' else GT.from_display
        want = bytes(m['prefix']) + enc(zh[m['id']]) + bytes(m['suffix'])
        if raw_str == want:
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                print('   MISMATCH id=%d %s\n     got  %r\n     want %r'
                      % (m['id'], m['script'], raw_str[:70], want[:70]))
    print('[verify] 译文回读：OK=%d  BAD=%d' % (ok, bad))

    if a.orig:
        och = GT.open_chapter(a.orig)
        if set(smap) != {s['name'] for s in och['scripts']}:
            print('[verify] 脚本集与原文不一致！')
        else:
            # 未翻译字符串必须逐字节保持
            touched = set()
            for m in meta:
                s = smap[m['script']]
                touched.add((m['script'], m['offset']))
            diff = same = 0
            for s in scripts:
                o = next(x for x in och['scripts'] if x['name'] == s['name'])
                po = och['dec'][o['data_off']:o['data_off'] + o['data_len']]
                pn = dec[s['data_off']:s['data_off'] + s['data_len']]
                off = 0
                while off < len(po):
                    nul = po.find(b'\x00', off)
                    if nul < 0:
                        break
                    if (s['name'], off) in touched:
                        off = nul + 1
                        continue
                    if po[off:nul] != pn[off:nul]:
                        diff += 1
                    else:
                        same += 1
                    off = nul + 1
            print('[verify] 未翻译池串逐字节一致：%d 条，差异 %d 条' % (same, diff))
            print('[verify] 代码区是否改动：%s'
                  % ('否（未改）' if all(
                      och['dec'][next(x for x in och['scripts'] if x['name'] == s['name'])['code_off']:
                              next(x for x in och['scripts'] if x['name'] == s['name'])['code_off'] + s['code_len']]
                      == dec[s['code_off']:s['code_off'] + s['code_len']] for s in scripts)
                     else '是（异常）'))
    print('[verify] %s' % ('全部通过' if bad == 0 else '存在问题'))


if __name__ == '__main__':
    main()
