#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate tool/opcodelist.py from tool/_ops.json (produced by build_opcodes.py)."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
OPS = json.load(open(os.path.join(HERE, '_ops.json'), encoding='utf-8'))['ops']

HEADER = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opcodelist.py — GSX script-VM opcode dictionary ("The Beautiful World", Enterbrain 2011)

Recovered from the disassembly of the chapter-1 executable:

  * The VM's handler table lives at  `DAT_00ed2ec0`, an array of 1024 function
    pointers filled in by the VM initialiser (VA 0x00459520).
  * The interpreter loop (VA 0x00437xx0, GsxVm.cpp) does:
        w      = next 32-bit word of the code array          (FUN_00466700)
        op     = w & 0xFFFF                                  // opcode index
        inline = w >> 16                                     // inline argument
        if handler_table[op] == 0  -> undefined opcode (VM aborts)
        else handler( inline )                               // push i.e. thiscall
  * Handlers fetch their own operands from the same word stream through
    FUN_00466700, so instructions are variable length.

`arity` = number of extra 32-bit words the handler consumes.  It was obtained by
counting the FUN_00466700 call sites reachable from each handler and then
refined by a constraint solver (tool/solve_arity.py) that had to segment all
509 scripts of the three chapters exactly.
"""

OPCODES = {
'''

FOOTER = '''}

# opcode index -> mnemonic
BY_NAME = {v['mnemonic']: k for k, v in OPCODES.items()}

# word masks used by the interpreter
OP_MASK = 0xFFFF
ARG_SHIFT = 16


def lookup(op):
    return OPCODES.get(op)


def mnemonic(op):
    e = OPCODES.get(op)
    return e['mnemonic'] if e else 'OP_UNKNOWN_%d' % op
'''

with open(os.path.join(HERE, 'opcodelist.py'), 'w', encoding='utf-8') as f:
    f.write(HEADER)
    for k in sorted(OPS, key=int):
        e = OPS[k]
        f.write("    %-5s: {'mnemonic': %-12s 'handler': 0x%06x, 'module': %-26s 'arity': %d},\n"
                % (k, "'%s'," % e['mnemonic'], e['handler'], "'%s'," % e['module'], e['arity']))
    f.write(FOOTER)
print('opcodelist.py written with %d opcodes' % len(OPS))
