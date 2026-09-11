# -*- coding: utf-8 -*-
"""Thin compatibility shim — all real logic lives in cod.py / fpack.py.

Kept so that the arity-recovery scripts (deriv_arity.py / solve_arity.py)
stay reproducible.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cod import (load_table, make_inverse, decrypt, encrypt, find_table_va,
                 parse_cod, build_cod, read_pack, get_file, MAGIC, HDR)

EXE = 'Beautiful World 1.1/Beautiful World １章.exe'
PACK = 'Beautiful World 1.1/filepack.bin'
