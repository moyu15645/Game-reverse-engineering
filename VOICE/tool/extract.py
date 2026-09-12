"""LOVELESS 文本提取器 / 注入器

提取：扫描每个 .ovl 的文本区，按 0x0A（换行操作码）归并为「逻辑句」，
输出 text/<name>.json + text/<name>.json.meta.json。

注入：读取译文（message 字段），按「定长就地替换」写回 .ovl。
每个物理文本区长度固定，译文不足处用 0x20 补齐，超出则该行标记 overflow。

用法:
  python extract.py extract <unpacksrcdir> <outdir>          # 全量提取
  python extract.py extract <unpacksrcdir> <outdir> <glob>   # 只处理匹配文件
  python extract.py inject  <srcdir> <transdir> <outdir>     # 注入重建
  python extract.py stats   <srcdir>
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ovl_text import meaningful_runs, dbl_byte_count

NEWLINE_OP = b'\x0a'


def build_messages(data):
    """Return list of messages. Each message = list of physical runs."""
    runs = meaningful_runs(data, min_len=4, min_dbl=2)
    msgs = []
    cur = None
    last_end = None
    for r in runs:
        if cur is not None and last_end is not None and data[last_end:r['start']] == NEWLINE_OP:
            cur.append(r)
        else:
            if cur:
                msgs.append(cur)
            cur = [r]
        last_end = r['start'] + r['length']
    if cur:
        msgs.append(cur)
    return msgs


def run_kind(r):
    """classify a standalone run: dialogue / narration / nametable entry"""
    if r['pre'].endswith(b'\xff\xff') and r['post'].startswith(b'\x00\xff\xff'):
        return 'namelist'
    return 'text'


def do_extract(srcdir, outdir, pattern='*'):
    os.makedirs(outdir, exist_ok=True)
    total = 0
    files = 0
    for p in sorted(glob.glob(os.path.join(srcdir, pattern))):
        name = os.path.basename(p)
        if name.startswith('_') or not name.lower().endswith('.ovl'):
            continue
        data = open(p, 'rb').read()
        msgs = build_messages(data)
        if not msgs:
            continue
        entries = []
        meta = []
        idx = 0
        for m in msgs:
            pre_jp = '\n'.join(x['text'] for x in m)
            slots = [{'off': x['start'], 'len': x['length'],
                      'jp': x['text'],
                      'kind': run_kind(x) if len(m) == 1 else 'text'} for x in m]
            kind = slots[0]['kind'] if len(slots) == 1 else 'text'
            e = {'id': idx, 'pre_jp': pre_jp, 'message': pre_jp}
            entries.append(e)
            meta.append({'id': idx, 'kind': kind,
                         'total_len': sum(s['len'] for s in slots),
                         'slots': [{'off': s['off'], 'len': s['len'], 'jp': s['jp']}
                                   for s in slots]})
            idx += 1
        base = os.path.splitext(name)[0]
        with open(os.path.join(outdir, base + '.json'), 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
        with open(os.path.join(outdir, base + '.json.meta.json'), 'w', encoding='utf-8') as f:
            json.dump({'file': name, 'size': len(data), 'entries': meta}, f,
                      ensure_ascii=False, indent=1)
        total += len(entries)
        files += 1
    print('extracted %d messages from %d files' % (total, files))
    return total, files


def do_inject(srcdir, transdir, outdir, use_map=False):
    os.makedirs(outdir, exist_ok=True)
    import hanzi_map
    table = hanzi_map.load() if use_map else {}
    nfile = nentry = nover = 0
    overflow = []
    dropped_all = {}
    for p in sorted(glob.glob(os.path.join(transdir, '*.json'))):
        base = os.path.basename(p)
        if base.endswith('.meta.json'):
            continue
        stem = base[:-5]
        meta_p = os.path.join(transdir, stem + '.json.meta.json')
        src_p = os.path.join(srcdir, stem + '.OVL')
        if not os.path.exists(src_p):
            src_p = os.path.join(srcdir, stem + '.ovl')
        if not os.path.exists(meta_p) or not os.path.exists(src_p):
            continue
        trans = json.load(open(p, encoding='utf-8'))
        meta = json.load(open(meta_p, encoding='utf-8'))
        data = bytearray(open(src_p, 'rb').read())
        by_id = {e['id']: e for e in trans}
        for m in meta['entries']:
            t = by_id.get(m['id'])
            if not t:
                continue
            msg = t.get('message', '')
            if msg == t.get('pre_jp'):
                continue                     # untranslated -> keep original
            if use_map:
                msg = hanzi_map.convert(msg, table)
            slots = m['slots']
            fixed = _place(msg, slots, dropped_all)
            if fixed is None:
                nover += 1
                overflow.append((stem, m['id'], sum(s['len'] for s in slots),
                                 len(msg.encode('cp932', 'ignore'))))
                continue
            for s, blob in zip(slots, fixed):
                data[s['off']:s['off'] + s['len']] = blob
            nentry += 1
        out_p = os.path.join(outdir, stem + '.OVL')
        open(out_p, 'wb').write(bytes(data))
        nfile += 1
    print('injected %d entries into %d files (%d overflow, map=%s)'
          % (nentry, nfile, nover, use_map))
    if dropped_all:
        with open(os.path.join(outdir, '_dropped_chars.json'), 'w', encoding='utf-8') as f:
            json.dump(dropped_all, f, ensure_ascii=False, indent=1)
        print('dropped chars ->  _dropped_chars.json (%d kinds)' % len(dropped_all))
    if overflow:
        with open(os.path.join(outdir, '_overflow.json'), 'w', encoding='utf-8') as f:
            json.dump(overflow, f, ensure_ascii=False, indent=1)
        print('overflow list -> _overflow.json')
    return nfile, nentry, nover


def _place(msg, slots, dropped=None):
    """distribute the translated text over the fixed-size physical slots."""
    text = msg
    out = []
    pos = 0
    for i, s in enumerate(slots):
        cap = s['len']
        cur = bytearray()
        while pos < len(text):
            ch = text[pos]
            try:
                b = ch.encode('cp932')
            except UnicodeEncodeError:
                if dropped is not None:
                    dropped[ch] = dropped.get(ch, 0) + 1
                pos += 1
                continue
            if len(cur) + len(b) > cap:
                break
            cur += b
            pos += 1
        if pos >= len(text) and i < len(slots) - 1:
            # everything already placed -> remaining slots get blank padding
            out.append(bytes(cur) + b'\x20' * (cap - len(cur)))
            for s2 in slots[i + 1:]:
                out.append(b'\x20' * s2['len'])
            return out
        out.append(bytes(cur) + b'\x20' * (cap - len(cur)))
    if pos < len(text):
        return None
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[0] == 'extract':
        do_extract(a[1], a[2], a[3] if len(a) > 3 else '*')
    elif a[0] == 'inject':
        do_inject(a[1], a[2], a[3], '--map' in a)
    else:
        print(__doc__)
