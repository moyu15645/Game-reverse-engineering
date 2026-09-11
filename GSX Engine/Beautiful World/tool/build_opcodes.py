#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build tool/opcodelist.py — the GSX VM opcode dictionary.

Sources of truth:
  * dispatch table (extracted from the exe's GsxVm init function)
  * handler source module (from the assert() file-name strings inside each handler)
  * operand arity (heuristic + constraint solver)
"""
import os, sys, re, json, struct, collections, bisect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cod
import deriv_arity as DA

MOD_SHORT = {
    'GsxVm': 'VM', 'GsxVm_MsgProcs': 'MSG', 'GsxVm_GraphicsProcs': 'GRA',
    'GsxVm_SaveProcs': 'SAV', 'GsxVm_SenProcs': 'SEN', 'GsxVm_SoundProcs': 'SND',
    'GsxVm_SpecialProcs': 'SPC', 'GsxVm_Util': 'UTL',
    'sys_scmes': 'MES', 'sys_scmes_core': 'MESC', 'sys_scmes_main': 'MESM',
    'sys_scmes_event': 'MESE', 'sys_scmesdisp': 'MESD', 'sys_scname': 'NAM',
    'sys_scselmesdisp': 'SEL', 'sys_scbg': 'BG', 'sys_scpict': 'PIC',
    'sys_scface': 'FAC', 'sys_scmask': 'MSK', 'sys_scdisp': 'DSP',
    'sys_scsp': 'SPR', 'sys_sc_csp': 'SPR', 'sys_scsys': 'SCS',
    'sys_scfonman': 'FNT', 'sys_scfont_sp': 'FNT', 'sys_scmove': 'MOV',
    'sys_scmove_vib': 'MOV', 'sys_scmove_scale_vib': 'MOV',
    'ComGame': 'GAME', 'ComGameScript': 'GAME', 'ComSave': 'SAVE',
    'ComOption': 'OPT', 'ComLog': 'LOG', 'FileLoad': 'LOAD',
    'task_script': 'TASK', 'task_script_ctrl': 'TASK', 'task_script_exec': 'TASK',
    'cGameData': 'GDAT',
}


def build():
    folder, exe = DA.CH['1.1']
    exe_path = os.path.join(folder, exe)
    data, imgbase, secs = cod.pe_sections(exe_path)

    def f2v(fo):
        for name, sva, rs, ra in secs:
            if ra <= fo < ra + rs:
                return sva + (fo - ra)
        return None

    # 1. all ".\\src\\....cpp" string VAs
    srcstr = {}
    for m in re.finditer(rb'\.[\\/]src[\\/][\x20-\x7e]*?\.cpp', data):
        va = f2v(m.start())
        if va:
            srcstr[va] = m.group().decode('latin1').replace('\\', '/').split('/')[-1][:-4]

    # 2. function boundaries
    funcs = DA.load_funcs()
    starts = [f[0] for f in funcs]

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        return funcs[i][0] if i >= 0 else None

    # function end = next start (approximation)
    ends = {}
    for i, (a, n) in enumerate(funcs):
        ends[a] = funcs[i + 1][0] if i + 1 < len(funcs) else a + 0x4000

    # 3. find push-imm references to those strings, map to owning function
    mod = {}
    for m in re.finditer(rb'\x68(.{4})', data, re.S):
        va = struct.unpack('<I', m.group(1))[0]
        if va in srcstr:
            f = owner(f2v(m.start()))
            if f:
                # keep the module of the function that asserts most often
                mod.setdefault(f, collections.Counter())[srcstr[va]] += 1
    funcmod = {f: c.most_common(1)[0][0] for f, c in mod.items()}

    M = json.load(open(os.path.join(HERE, '_opcodemodel.json')))
    arity = {int(k): v for k, v in M['arity'].items()}
    disp = {int(k): v for k, v in M['dispatch'].items()}

    # MSVC emits a translation unit's functions contiguously: for handlers with
    # no assert() string, inherit the module of the nearest preceding function
    # that does have one.
    known = sorted(funcmod.items())
    kaddrs = [k for k, _ in known]

    def mod_of(f):
        if f in funcmod:
            return funcmod[f]
        i = bisect.bisect_right(kaddrs, f) - 1
        return known[i][1] if i >= 0 else 'GsxVm'

    ops = {}
    for op, h in sorted(disp.items()):
        m = mod_of(h)
        short = MOD_SHORT.get(m, re.sub(r'[^A-Za-z0-9]', '', m)[:4].upper())
        ops[op] = {'opcode': op, 'handler': h, 'module': m, 'arity': arity.get(op, 0),
                   'mnemonic': '%s_%d' % (short, op)}
    json.dump({'ops': {str(k): v for k, v in ops.items()}, 'imgbase': imgbase},
              open(os.path.join(HERE, '_ops.json'), 'w'), indent=1)

    # human-readable module list
    print('%-5s %-10s %-9s %-6s %s' % ('op', 'mnemonic', 'handler', 'arity', 'module'))
    bymod = collections.Counter()
    for op, o in ops.items():
        bymod[o['module']] += 1
    for op, o in list(ops.items())[:40]:
        print('%-5d %-10s 0x%06x  %-6d %s' % (op, o['mnemonic'], o['handler'], o['arity'], o['module']))
    print('...')
    print('modules:', dict(bymod.most_common()))


if __name__ == '__main__':
    build()
