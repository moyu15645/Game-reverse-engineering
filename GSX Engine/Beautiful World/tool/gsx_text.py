#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gsx_text.py — dialogue extraction / injection for the GSX visual-novel engine
("The Beautiful World", Enterbrain, 2011 — CoD.cpt script container)

CONTAINER MODEL
---------------
filepack.bin  (No-FilePlus "named" archive)
      └── /Data/script/CoD.cpt    (scenario container, stream-cipher obfuscated)
                ├── header       magic 00 04 05 01, count, 0xF8 pad          (0x100 B)
                ├── index[count] { u32 rel_off; u32 payload_size; u32; u32 } (16 B each)
                └── script blocks, each:
                        +0x00 u32 code_words     (number of 32-bit VM words)
                        +0x04 u32 data_size      (size of the string pool)
                        +0x08 8B reserved
                        +0x10 char name[16]
                        +0x20 16B reserved
                        +0x30 16B reserved
                        +0x40 code  : code_words * 4 bytes
                        +0x40+4*code_words : data pool (NUL-terminated strings)

The VM executes the *code* array; text lives in the *data* pool and is
referenced BY BYTE OFFSET.  Every pool string ends with 0x0C followed by
0x0D or 0x0E, then a NUL terminator.

INJECTION STRATEGY (方案 A — fixed offsets)
------------------------------------------
Because the code stream addresses pool strings by absolute byte offset, the
pool layout is kept byte-identical: each translated string is written back at
its ORIGINAL offset, and the slack between its new NUL terminator and the next
string's offset is zero-filled (never read).  A translation must therefore fit
inside its original slot; the budget is reported per entry.

Usage
-----
  python gsx_text.py extract <chapter_dir>  -o <out.json> [--meta <meta.json>]
  python gsx_text.py inject  <chapter_dir>  --json <translated.json> --meta <meta.json> \
                             -o <new_cod.cpt> [--force]
  python gsx_text.py stats   <chapter_dir>
"""
import os, sys, json, struct, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod
import fpack

CH = {
    '1.1': ('Beautiful World 1.1', 'Beautiful World １章.exe'),
    '1.2': ('Beautiful World 1.2', 'Beautiful World ２章.exe'),
    '1.3': ('Beautiful World 1.3', 'Beautiful World 最終章.exe'),
}

COD_PATH = '/Data/script/CoD.cpt'

# ------------------------------------------------------------------ control codes
CC_END = 0x0C          # end-of-message marker (always present, before terminator)
CC_LF = 0x0D           # line break / new page
CC_END2 = 0x0E         # alternate end-of-message (page end)
CC_SEP = 0x0F          # paragraph separator inside one entry
CC_RUBY_A = 0x05       # ruby base start
CC_RUBY_B = 0x06       # ruby reading start
CC_RUBY_C = 0x07       # ruby end

# NOTE: 0x19 is the "the next byte is half-width ASCII" prefix used INSIDE the
# text (0x19 + one byte).  It must NOT be treated as a leading/trailing control
# byte: if the original string happened to start with a half-width character and
# the translation starts with a full-width one, keeping the stale 0x19 prefix
# makes the engine's message parser skip one byte of the real text and then run
# off into the middle of characters -- which lands on ASCII 0x20..0x31 and is
# executed as a FACE/POSE command (`isSetupFacePose` assert, sys_scmes.cpp:6141).
# from_display() re-adds 0x19 for every ASCII byte, so nothing is lost.
CTRL = {0x0C, 0x0D, 0x0E, 0x0F, 0x09, 0x0A}

PH_LF = '[[BR]]'       # was 0x0D  (line break / new page)
PH_SEP = '[[SP]]'      # was 0x0F  (paragraph separator)
PH_HW = 0x19           # half-width ASCII prefix


def split_controls(raw: bytes):
    """Return (prefix_bytes, core_bytes, suffix_bytes)."""
    i = 0
    while i < len(raw) and raw[i] in CTRL:
        i += 1
    j = len(raw)
    while j > i and raw[j - 1] in CTRL:
        j -= 1
    return raw[:i], raw[i:j], raw[j:]


def strip_ruby(core: bytes) -> bytes:
    """0x05 base 0x06 reading 0x07  ->  base   (furigana is dropped for Chinese)."""
    out = bytearray()
    i = 0
    n = len(core)
    while i < n:
        if core[i] == CC_RUBY_A:
            j = i + 1
            while j < n and core[j] not in (CC_RUBY_B, CC_RUBY_C):
                j += 1
            out += core[i + 1:j]              # keep the base text only
            while j < n and core[j] != CC_RUBY_C:
                j += 1
            i = j + 1
            continue
        out.append(core[i])
        i += 1
    return bytes(out)


def to_display(core: bytes) -> str:
    """cp932 core bytes -> displayable string with ASCII-safe placeholders."""
    core = strip_ruby(core)
    out = bytearray()
    i = 0
    while i < len(core):
        b = core[i]
        if b == PH_HW:                 # half-width prefix: drop it, keep the ASCII
            i += 1
            continue
        if b == CC_LF:
            out += PH_LF.encode('ascii')
        elif b == CC_SEP:
            out += PH_SEP.encode('ascii')
        elif b < 0x20:
            i += 1
            continue
        else:
            out.append(b)
        i += 1
    return out.decode('cp932', errors='replace')


def from_display(text: str) -> bytes:
    """Displayable string -> core bytes (restore placeholders + half-width prefix)."""
    text = text.replace(PH_LF, '\x0d').replace(PH_SEP, '\x0f')
    text = text.replace(PH_LF.lower(), '\x0d').replace(PH_SEP.lower(), '\x0f')
    out = bytearray()
    for ch in text:
        enc = ch.encode('cp932')
        # The engine's message parser advances TWO bytes per rendered glyph, so
        # every single-byte character (ASCII *and* half-width katakana) has to be
        # written as 0x19 + byte; otherwise the parser eats the next byte too and
        # runs off into the middle of the text (see check_message_syntax.py).
        if len(enc) == 1 and (0x20 <= enc[0] < 0x7F or 0xA1 <= enc[0] <= 0xDF):
            out.append(PH_HW)
        out += enc
    return bytes(out)


def from_display_raw(text: str) -> bytes:
    """cp932 without the 0x19 half-width prefix (name table / non-message data)."""
    return text.replace(PH_LF, '\x0d').replace(PH_SEP, '\x0f').encode('cp932')


def is_resource_name(raw: bytes) -> bool:
    """Pure printable ASCII -> a resource name / keyword, not translatable text."""
    if not raw:
        return True
    return all(0x20 <= b < 0x7F for b in raw)


def has_japanese(raw: bytes) -> bool:
    try:
        s = raw.decode('cp932')
    except UnicodeDecodeError:
        return False
    for c in s:
        if '\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff' or '\uff66' <= c <= '\uff9f':
            return True
    return False


# ------------------------------------------------------------------ chapter access
def open_chapter(chdir):
    exe = None
    for fn in os.listdir(chdir):
        if fn.lower().endswith('.exe') and 'uninst' not in fn.lower():
            exe = os.path.join(chdir, fn)
    pack = os.path.join(chdir, 'filepack.bin')
    tbl = cod.load_table(exe)
    inv = cod.make_inverse(tbl)
    raw, info = cod.get_file(pack, COD_PATH)
    dec = cod.decrypt(raw, tbl)
    magic, count, scripts = cod.parse_cod(dec)
    return dict(exe=exe, pack=pack, tbl=tbl, inv=inv, raw=raw, dec=dec,
                magic=magic, count=count, scripts=scripts)


# ------------------------------------------------------------------ extract
def iter_pool_strings(ch):
    """Yield (script, offset, slot, raw) for every NUL-terminated pool string."""
    d = ch['dec']
    for s in ch['scripts']:
        pool = d[s['data_off']:s['data_off'] + s['data_len']]
        off = 0
        while off < len(pool):
            nul = pool.find(b'\x00', off)
            if nul < 0:
                break
            yield s['name'], off, nul - off + 1, pool[off:nul]
            off = nul + 1


def extract(chdir, out_json, meta_json, chapter):
    ch = open_chapter(chdir)
    entries, meta = [], []
    for script, off, slot, raw in iter_pool_strings(ch):
        if not raw or is_resource_name(raw) or not has_japanese(raw):
            continue
        pre, core, suf = split_controls(raw)
        disp = to_display(core)
        if not disp.strip():
            continue
        eid = len(entries)
        entries.append({'id': eid, 'script': script, 'pre_jp': disp, 'message': ''})
        meta.append({'id': eid, 'script': script, 'offset': off, 'slot': slot,
                     'prefix': list(pre), 'suffix': list(suf),
                     'orig_len': len(raw), 'budget': slot - 1 - len(pre) - len(suf),
                     'kind': 'text'})
    json.dump(entries, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump({'chapter': chapter, 'file': COD_PATH, 'count': len(entries),
               'entries': meta},
              open(meta_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[extract] %s : %d translatable strings -> %s' % (chdir, len(entries), out_json))
    return entries, meta


def extract_names(chdir, out_json):
    """Pull the speaker-name table out of the `init_app` script (a run of short
    strings terminated by literal 'NULL')."""
    ch = open_chapter(chdir)
    out = []
    for script, off, slot, raw in iter_pool_strings(ch):
        if script != 'init_app' or not raw or is_resource_name(raw):
            continue
        pre, core, suf = split_controls(raw)
        disp = to_display(core)
        if disp and disp != 'NULL':
            out.append(disp)
    json.dump(out, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[names] %s : %d speaker names -> %s' % (chdir, len(out), out_json))
    return out


# ------------------------------------------------------------------ inject
def inject(chdir, json_path, meta_path, out_path, force=False, quiet=False):
    ch = open_chapter(chdir)
    d = bytearray(ch['dec'])
    trans = {}
    for e in json.load(open(json_path, encoding='utf-8')):
        trans[e['id']] = e.get('message') or ''
    meta = json.load(open(meta_path, encoding='utf-8'))['entries']
    smap = {s['name']: s for s in ch['scripts']}
    overflow, missing, encfail, ok = [], [], [], 0
    for m in meta:
        t = trans.get(m['id'])
        if not t:
            missing.append(m['id'])
            continue
        pre = bytes(m['prefix'])
        suf = bytes(m['suffix'])
        try:
            # the speaker-name table (init_app) is a plain string list: the engine
            # never runs the message parser over it, and the original file stores
            # bare ASCII there (学生A) -- so do not add the 0x19 prefix
            core = (from_display_raw if m.get('script') == 'init_app'
                    else from_display)(t)
        except UnicodeEncodeError as e:
            encfail.append((m['id'], m['script'], str(e)))
            continue
        new = pre + core + suf
        if len(new) + 1 > m['slot']:
            if force:
                room = m['slot'] - 1 - len(pre) - len(suf)
                core = core[:max(0, room)]
                while core:
                    try:
                        core.decode('cp932')
                        break
                    except UnicodeDecodeError:
                        core = core[:-1]
                overflow.append((m['id'], m['script'], len(new), m['slot']))
                new = pre + core + suf
            else:
                overflow.append((m['id'], m['script'], len(new), m['slot']))
                continue
        s = smap[m['script']]
        base = s['data_off'] + m['offset']
        d[base:base + m['slot']] = new + b'\x00' * (m['slot'] - len(new))
        ok += 1
    if missing:
        print('[warn] %d ids had no translation (original kept)' % len(missing))
    for f in encfail[:10]:
        print('[ERROR] id %d (%s) not encodable to cp932: %s' % f)
    if overflow:
        print('[warn] %d entries exceed their slot%s' % (len(overflow), ' (truncated)' if force else ''))
        for o in overflow[:20]:
            print('        id=%d %s need=%d slot=%d' % o)
    out = cod.encrypt(bytes(d), ch['tbl'], ch['inv'])
    open(out_path, 'wb').write(out)
    if not quiet:
        print('[inject] %d/%d entries written -> %s (%d bytes, was %d)'
              % (ok, len(meta), out_path, len(out), len(ch['raw'])))
    return dict(ok=ok, overflow=len(overflow), encfail=len(encfail), out=out_path)


# ------------------------------------------------------------------ stats
def stats(chdir):
    ch = open_chapter(chdir)
    d = ch['dec']
    total = jp = ascii = 0
    for s in ch['scripts']:
        pool = d[s['data_off']:s['data_off'] + s['data_len']]
        off = 0
        while off < len(pool):
            nul = pool.find(b'\x00', off)
            if nul < 0:
                break
            raw = pool[off:nul]
            if raw:
                total += 1
                if is_resource_name(raw):
                    ascii += 1
                elif has_japanese(raw):
                    jp += 1
            off = nul + 1
    print('%s : scripts=%d pool_strings=%d japanese=%d ascii=%d'
          % (chdir, len(ch['scripts']), total, jp, ascii))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('extract')
    p.add_argument('chapter_dir')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--meta', required=True)
    p.add_argument('--chapter', default='')
    p = sub.add_parser('inject')
    p.add_argument('chapter_dir')
    p.add_argument('--json', required=True)
    p.add_argument('--meta', required=True)
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--force', action='store_true')
    p = sub.add_parser('names')
    p.add_argument('chapter_dir')
    p.add_argument('-o', '--out', required=True)
    p = sub.add_parser('stats')
    p.add_argument('chapter_dir')
    a = ap.parse_args()
    if a.cmd == 'extract':
        extract(a.chapter_dir, a.out, a.meta, a.chapter)
    elif a.cmd == 'inject':
        inject(a.chapter_dir, a.json, a.meta, a.out, a.force)
    elif a.cmd == 'names':
        extract_names(a.chapter_dir, a.out)
    else:
        stats(a.chapter_dir)


if __name__ == '__main__':
    main()
