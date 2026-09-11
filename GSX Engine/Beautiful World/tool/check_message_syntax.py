#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_message_syntax.py — validate every injected string against the engine's
message parser.

`FUN_0046c460` (sys_scmes.cpp) walks a message byte by byte and treats

  * 0x01..0x1F            as control codes (0x0C/0x0E = page end, 0x0D/0x0F =
                          line/paragraph break, 0x19 = "next byte is half-width")
  * ASCII 0x20..0x31      as FACE / POSE commands (`!` `"` `$` `%` `&` `'` `(`
                          `)` `*` `+` `,` `-` `.` `/` `0` `1`, plus space and #)
  * anything else         as text

Two ways a translated string can crash the game:

  1. a bare ASCII byte in 0x20..0x31 — executed as a command; `*`..`1` assert
     `isSetupFacePose` (sys_scmes.cpp:6141) because no face was set up;
  2. a 0x19 that is **not** followed by an ASCII byte — the parser skips 0x19
     plus one byte, lands in the middle of a multi-byte character, and then
     mis-reads the following bytes (which eventually hits case 1).

Rule 1 is why every ASCII byte must be written as `0x19 + byte`.
Rule 2 is why a stale leading 0x19 must never be kept as a "prefix control byte"
when the translation no longer starts with a half-width character.

Usage
-----
  python tool/check_message_syntax.py                 # all three releases
  python tool/check_message_syntax.py --variant 1.1
"""
import os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gsx_text as G

CMD = set(range(0x20, 0x32))
CH = [('1.1', 'release/Beautiful World 1.1'),
      ('1.2', 'release/Beautiful World 1.2'),
      ('1.3', 'release/Beautiful World 1.3')]


def is_lead(b):
    """first byte of a 2-byte cp932 character"""
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC   # incl. NEC/IBM extensions


def is_single(b):
    """a character that occupies exactly one byte (ASCII / half-width katakana)"""
    return (0x20 <= b < 0x7F or 0xA1 <= b <= 0xDF)


def is_resource(raw):
    return raw and all(0x20 <= b < 0x7F for b in raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default=None)
    a = ap.parse_args()

    bad = 0
    for variant, folder in CH:
        if a.variant and a.variant != variant:
            continue
        path = os.path.join(ROOT, folder)
        if not os.path.isdir(path):
            continue
        ch = G.open_chapter(path)
        d = ch['dec']
        n_msg = 0
        errs = []
        for s in ch['scripts']:
            pool = d[s['data_off']:s['data_off'] + s['data_len']]
            off = 0
            if s['name'] == 'init_app':
                # speaker-name table: a plain string list, never fed to the
                # message parser (the original stores bare ASCII here: 学生A)
                continue
            while off < len(pool):
                nul = pool.find(b'\x00', off)
                if nul < 0:
                    break
                raw = pool[off:nul]
                off = nul + 1
                if not raw or is_resource(raw):
                    continue
                n_msg += 1
                # simulate the parser
                i = 0
                while i < len(raw):
                    b = raw[i]
                    if b == 0x19:
                        if i + 1 >= len(raw) or not is_single(raw[i + 1]):
                            errs.append((s['name'], off, '0x19 not followed by ASCII',
                                         raw[max(0, i - 8):i + 8].hex(' ')))
                            break
                        i += 2
                        continue
                    if b in CMD:
                        errs.append((s['name'], off, 'bare command ASCII 0x%02x' % b,
                                     raw[max(0, i - 8):i + 8].hex(' ')))
                        break
                    if b < 0x20:
                        i += 1
                        continue
                    if is_lead(b):                   # 2-byte cp932 character
                        i += 2
                        continue
                    if is_single(b):                 # must have been 0x19-prefixed
                        errs.append((s['name'], off,
                                     'bare single-byte 0x%02x' % b,
                                     raw[max(0, i - 8):i + 8].hex(' ')))
                        break
                    i += 1
        print('=== %s : %d message strings, %d problem strings' % (folder, n_msg, len(errs)))
        seen = set()
        for name, off, why, ctx in errs:
            key = (name, off)
            if key in seen:
                continue
            seen.add(key)
            print('    %s @rel %d : %s   %s' % (name, off, why, ctx))
            if len(seen) >= 12:
                break
        bad += len(seen)
    print()
    print('TOTAL problem strings: %d' % bad)
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
