#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constraint solver for GSX instruction arity (iterative DFS).

Start from a heuristic arity table, then for every script search for a
segmentation that consumes the word stream EXACTLY.  Deviations from the
heuristic are bounded (±MAXDEV) and collected as votes; the votes are folded
back into the global table until every script of every chapter segments.
"""
import os, sys, json, struct, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod
import deriv_arity as DA

MAXDEV = 3
KMAX = 12


def load_scripts():
    out = {}
    for ch, (folder, exe) in DA.CH.items():
        tbl = cod.load_table(os.path.join(folder, exe))
        raw, _ = cod.get_file(os.path.join(folder, 'filepack.bin'), '/Data/script/CoD.cpt')
        d = cod.decrypt(raw, tbl)
        m, c, scripts = cod.parse_cod(d)
        lst = []
        for s in scripts:
            w = list(struct.unpack_from('<%dI' % s['code_words'], d, s['code_off']))
            lst.append((s['name'], w))
        out[ch] = lst
    return out


def solve(words, kg, disp, maxdev=MAXDEV):
    n = len(words)
    dead = set()
    frames = []
    pos = 0

    def back():
        while frames:
            f = frames[-1]
            f['ci'] += 1
            if f['ci'] < len(f['cands']):
                f['kk'] = f['cands'][f['ci']]
                return f['pos'] + 1 + f['kk']
            dead.add(f['pos'])
            frames.pop()
        return None

    while True:
        if pos == n:
            return [(f['pos'], f['op'], f['kk']) for f in frames]
        if pos > n or pos in dead:
            pos = back()
            if pos is None:
                return None
            continue
        op = words[pos] & 0xffff
        if op not in disp:
            dead.add(pos)
            pos = back()
            if pos is None:
                return None
            continue
        g = min(kg.get(op, 0), KMAX)
        cands = [c for c in range(0, KMAX + 1) if abs(c - g) <= maxdev and pos + 1 + c <= n]
        cands.sort(key=lambda c: (abs(c - g), c))
        if not cands:
            dead.add(pos)
            pos = back()
            if pos is None:
                return None
            continue
        frames.append({'pos': pos, 'op': op, 'cands': cands, 'ci': 0, 'kk': cands[0]})
        pos = pos + 1 + cands[0]


def greedy_ok(words, kg, disp):
    i, n = 0, len(words)
    while i < n:
        op = words[i] & 0xffff
        if op not in disp:
            return False
        i += 1 + kg.get(op, 0)
        if i > n:
            return False
    return i == n


def score(scripts, kg, disp):
    return sum(1 for ch, lst in scripts.items() for n, w in lst if greedy_ok(w, kg, disp))


def main():
    scripts = load_scripts()
    kg, disp = DA.derive(os.path.join(*DA.CH['1.1']))
    total = sum(len(l) for l in scripts.values())
    print('seed arity:', {o: kg[o] for o in (0, 82, 84, 114, 290, 296)})
    print('seed greedy-solvable: %d/%d' % (score(scripts, kg, disp), total))

    for it in range(25):
        votes = collections.defaultdict(collections.Counter)
        for ch, lst in scripts.items():
            for name, w in lst:
                r = solve(w, kg, disp)
                if r is None:
                    continue
                for pos, op, kk in r:
                    votes[op][kk] += 1
        base = score(scripts, kg, disp)
        improved = 0
        for op, cnt in sorted(votes.items()):
            if len(cnt) == 1 and kg.get(op, 0) == cnt.most_common(1)[0][0]:
                continue
            cur = kg.get(op, 0)
            best_v, best_s = cur, base
            for v, c in cnt.most_common(4):
                if v == cur:
                    continue
                kg[op] = v
                s = score(scripts, kg, disp)
                if s > best_s:
                    best_v, best_s = v, s
            kg[op] = best_v
            if best_v != cur:
                improved += 1
                print('  it%02d op %-4d arity %d -> %d   greedy %d->%d' %
                      (it, op, cur, best_v, base, best_s))
        newscore = score(scripts, kg, disp)
        print('  it%02d improved=%d greedy-solvable %d/%d' % (it, improved, newscore, total))
        if improved == 0:
            break

    ok = bad = 0
    fails = []
    for ch, lst in scripts.items():
        for name, w in lst:
            if greedy_ok(w, kg, disp):
                ok += 1
            else:
                bad += 1
                fails.append((ch, name))
    print('FINAL greedy ok=%d bad=%d' % (ok, bad))
    for f in fails[:25]:
        print('  ', f)
    json.dump({'arity': {str(o): v for o, v in kg.items()},
               'dispatch': {str(o): v for o, v in disp.items()}},
              open(os.path.join(HERE, '_opcodemodel.json'), 'w'))
    print('opcodes with arity>0:', sum(1 for v in kg.values() if v))


if __name__ == '__main__':
    main()
