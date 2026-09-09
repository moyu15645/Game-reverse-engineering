# -*- coding: utf-8 -*-
"""
opcodelist.py — ISM 脚本虚拟机（ISM Script VM）完整操作码定义。

引擎背景
--------
《タイムカプセル “春”》的脚本 VM 由 ism.dll 实现（tcs.exe 仅为主程序壳）。
脚本文件扩展名 .ISM，打包于 data.isa 封包（magic "ISM ARCHIVED"）内。

VM 特征
-------
* 单字节主操作码、栈式虚拟机（操作数走 VM 数据栈，栈指针 [0x100680f8]）。
* 指令指针（IP）存于 [0x100640e4]，分发器主循环 0x1000145d，
  通过 call [ecx*4 + 0x100a4a38] 动态分发到各 handler。
* 静态操作码表位于 0x10037420（123 个 opcode → 113 个唯一 handler，
  以 0xFFFFFFFF 终止）。
* 每条指令在推进 IP 后，多数 handler 会检查 byte ptr [IP] == 0x05，
  若相等则再 inc IP 一次——0x05 是「语句分隔符/终止符」，可视为 SEP 操作码。
* 字符串内联加密（op_33）：逐字节  not dl; xor dl, al 解密，
  密钥 = (字符串操作码文件偏移 - code_base) & 0xFF，若为 0xFF 则归零。

本文件是反汇编器与汇编器的唯一真值源。
"""

# ─────────────────────────────────────────────────────────────────────────────
# 操作数类型（operand type）
# ─────────────────────────────────────────────────────────────────────────────
# none      : 无操作数（仅 opcode 字节）
# imm32     : 32 位立即数（小端 u32，原样嵌入指令流）
# rel32     : 32 位相对跳转偏移（基准 = 当前指令（opcode 字节）的文件偏移）
# table     : 32 位 code-relative 偏移，指向数据区的 switch 表（基准 = code_base）
# strref    : 32 位 code-relative 偏移，指向代码区某个 op_33 字符串（基准 = code_base）
# str       : 变长内联字符串（op_33 专用，长度由后续字节决定）
# u8        : 8 位无符号立即数

# ─────────────────────────────────────────────────────────────────────────────
# 完整操作码字典
# ─────────────────────────────────────────────────────────────────────────────
# 字段说明：
#   mnemonic  : 语义化助记符
#   length    : 指令总字节长度。int 为定长；'str' 为变长（仅 op_33）
#   operands  : 操作数列表，每项 {"type": ..., "bits": ...}
#   desc      : 语义说明（中文）
#   handler   : ism.dll 中的 handler 地址（佐证）
#
# 助记符命名约定：
#   控制流    JMP / JZ / RET / SWITCH / CALL
#   栈与常量  PUSHI / PUSH_VAR / SET_VAR
#   字符串    STR / STRREF
#   算术      ADD / SUB / MUL / DIV / MOD / SHIFT / BAND / BOR / XOR
#   逻辑      LAND / LOR / NOT / NEG
#   比较      EQ / NE / LT / GT / LE / GE
#   系统调用  SYS_xx（0x80–0xFF，引擎内建 API，逐条调用宿主 ism.dll 函数）
# ─────────────────────────────────────────────────────────────────────────────

OPCODES = {
    # ── 分隔符 / 空操作 ──────────────────────────────────────────────────────
    0x00: {"mnemonic": "NOP",  "length": 1, "operands": [], "desc": "空操作，推进 IP 并跳过后续 0x05", "handler": 0x10003D40},
    0x05: {"mnemonic": "SEP",  "length": 1, "operands": [], "desc": "语句分隔符/终止符", "handler": 0x10003CF0},
    0x06: {"mnemonic": "SEP",  "length": 1, "operands": [], "desc": "语句分隔符/终止符（0x05 别名）", "handler": 0x10003CF0},
    0x09: {"mnemonic": "OP_09","length": 1, "operands": [], "desc": "罕见（handler 0x10003810，未在样本中使用）", "handler": 0x10003810},
    0x22: {"mnemonic": "NOP",  "length": 1, "operands": [], "desc": "空操作（0x00 别名）", "handler": 0x10003D40},
    0x34: {"mnemonic": "OP_34","length": 1, "operands": [], "desc": "罕见（0x09 别名，未在样本中使用）", "handler": 0x10003810},

    # ── 算术（二元、类型分派，经 0x10003970 操作分发器） ───────────────────────
    0x0B: {"mnemonic": "SHIFT", "length": 1, "operands": [], "desc": "位移（左移/右移，按符号）", "handler": 0x10005D70},
    0x0C: {"mnemonic": "LAND",  "length": 1, "operands": [], "desc": "逻辑与（布尔）", "handler": 0x10006770},
    0x0D: {"mnemonic": "LOR",   "length": 1, "operands": [], "desc": "逻辑或（布尔）", "handler": 0x10006830},
    0x0E: {"mnemonic": "XOR",   "length": 1, "operands": [], "desc": "按位异或", "handler": 0x10005FA0},
    0x11: {"mnemonic": "ADD",   "length": 1, "operands": [], "desc": "加法（整数加 / 字符串拼接）", "handler": 0x10005A30},
    0x12: {"mnemonic": "SUB",   "length": 1, "operands": [], "desc": "减法", "handler": 0x10005BD0},
    0x13: {"mnemonic": "MUL",   "length": 1, "operands": [], "desc": "乘法", "handler": 0x10005CB0},
    0x14: {"mnemonic": "DIV",   "length": 1, "operands": [], "desc": "除法", "handler": 0x10006050},
    0x15: {"mnemonic": "MOD",   "length": 1, "operands": [], "desc": "取模", "handler": 0x10006140},
    0x1C: {"mnemonic": "BAND",  "length": 1, "operands": [], "desc": "按位与", "handler": 0x10005E40},
    0x1D: {"mnemonic": "BOR",   "length": 1, "operands": [], "desc": "按位或", "handler": 0x10005EF0},

    # ── 比较（结果压栈 0/1） ─────────────────────────────────────────────────
    0x16: {"mnemonic": "EQ",    "length": 1, "operands": [], "desc": "等于（==）", "handler": 0x10006230},
    0x17: {"mnemonic": "LT",    "length": 1, "operands": [], "desc": "小于（<）", "handler": 0x10006470},
    0x18: {"mnemonic": "GT",    "length": 1, "operands": [], "desc": "大于（>）", "handler": 0x10006530},
    0x19: {"mnemonic": "NE",    "length": 1, "operands": [], "desc": "不等于（!=）", "handler": 0x10006350},
    0x1A: {"mnemonic": "LE",    "length": 1, "operands": [], "desc": "小于等于（<=）", "handler": 0x100065F0},
    0x1B: {"mnemonic": "GE",    "length": 1, "operands": [], "desc": "大于等于（>=）", "handler": 0x100066B0},
    0x1E: {"mnemonic": "NOT",   "length": 1, "operands": [], "desc": "逻辑非", "handler": 0x100068F0},
    0x1F: {"mnemonic": "NEG",   "length": 1, "operands": [], "desc": "取负", "handler": 0x10006970},

    # ── 控制流 ──────────────────────────────────────────────────────────────
    0x0F: {"mnemonic": "SWITCH", "length": 9, "operands": [{"type": "table", "bits": 32}, {"type": "imm32", "bits": 32}],
           "desc": "多路分支（range/sparse 跳转表，见 vm_analysis.md）", "handler": 0x10005380},
    0x10: {"mnemonic": "INVOKE", "length": 1, "operands": [], "desc": "动态调用（经 0x10002090 执行脚本）", "handler": 0x10006BB0},
    0x20: {"mnemonic": "JMP",   "length": 5, "operands": [{"type": "rel32", "bits": 32}], "desc": "无条件相对跳转", "handler": 0x10005310},
    0x21: {"mnemonic": "JZ",    "length": 5, "operands": [{"type": "rel32", "bits": 32}], "desc": "栈顶为 0 时相对跳转", "handler": 0x10005320},
    0x24: {"mnemonic": "JMP",   "length": 5, "operands": [{"type": "rel32", "bits": 32}], "desc": "无条件相对跳转（0x20 别名）", "handler": 0x10005310},
    0x25: {"mnemonic": "RET",   "length": 1, "operands": [], "desc": "返回（从栈恢复 IP）", "handler": 0x10004070},
    0x28: {"mnemonic": "CALL",  "length": 1, "operands": [], "desc": "间接调用（函数索引在栈顶）", "handler": 0x10004930},

    # ── 栈与常量 / 变量访问 ──────────────────────────────────────────────────
    0x23: {"mnemonic": "PUSH_VAR", "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "压入变量值（按索引）", "handler": 0x10003FF0},
    0x30: {"mnemonic": "PUSHI",    "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "压入立即数", "handler": 0x10003DA0},
    0x31: {"mnemonic": "PUSHI",    "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "压入立即数（缩放变体，×[0x10037044]）", "handler": 0x10003DD0},
    0x32: {"mnemonic": "OP_32",    "length": 9, "operands": [{"type": "imm32", "bits": 32}, {"type": "imm32", "bits": 32}], "desc": "双 u32（未在样本中使用）", "handler": 0x10003F90},
    0x38: {"mnemonic": "PUSH_VAR", "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "压入变量值（经 0x10001fc0）", "handler": 0x10004E80},
    0x39: {"mnemonic": "PUSH_VAR", "length": 6, "operands": [{"type": "u8", "bits": 8}, {"type": "imm32", "bits": 32}], "desc": "压入变量值（u8 子索引 + u32）", "handler": 0x10004EC0},
    0x3A: {"mnemonic": "SET_VAR",  "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "弹出栈顶存入变量", "handler": 0x10006B00},
    0x3B: {"mnemonic": "SET_VAR",  "length": 6, "operands": [{"type": "u8", "bits": 8}, {"type": "imm32", "bits": 32}], "desc": "弹出栈顶存入变量（u8 子索引 + u32）", "handler": 0x100069F0},
    0x3C: {"mnemonic": "SET_VAR",  "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "变量写（未在样本中使用）", "handler": 0x10004FD0},
    0x3D: {"mnemonic": "SET_VAR",  "length": 6, "operands": [{"type": "u8", "bits": 8}, {"type": "imm32", "bits": 32}], "desc": "变量写（未在样本中使用）", "handler": 0x10005030},
    0x3E: {"mnemonic": "SET_VAR",  "length": 5, "operands": [{"type": "imm32", "bits": 32}], "desc": "弹出栈顶存入变量（0x3A 别名）", "handler": 0x10006B00},
    0x3F: {"mnemonic": "SET_VAR",  "length": 6, "operands": [{"type": "u8", "bits": 8}, {"type": "imm32", "bits": 32}], "desc": "弹出栈顶存入变量（0x3B 别名）", "handler": 0x100069F0},

    # ── 字符串 ──────────────────────────────────────────────────────────────
    0x33: {"mnemonic": "STR",   "length": "str", "operands": [{"type": "str", "bits": 0}], "desc": "内联字符串（加密，长度 <0xFF 用 1 字节长度，否则 u32 长度）", "handler": 0x10003E10},
    0x45: {"mnemonic": "STRREF", "length": 5, "operands": [{"type": "strref", "bits": 32}], "desc": "引用代码区某 op_33 字符串（人名池复用）", "handler": 0x10003EE0},

    # ── 比较/字符串操作（经 0x10003970，类型分派） ─────────────────────────────
    0x40: {"mnemonic": "TEST_BIT0", "length": 1, "operands": [], "desc": "测试第 0 位", "handler": 0x10004210},
    0x41: {"mnemonic": "TEST_BIT1", "length": 1, "operands": [], "desc": "测试第 1 位", "handler": 0x10004280},
    0x42: {"mnemonic": "TEST_BIT2", "length": 1, "operands": [], "desc": "测试第 2 位", "handler": 0x100042F0},
    0x43: {"mnemonic": "STRLEN",  "length": 1, "operands": [], "desc": "字符串长度/取子串", "handler": 0x10004440},
    0x44: {"mnemonic": "STR_OP",  "length": 1, "operands": [], "desc": "字符串操作", "handler": 0x10004570},
    0x46: {"mnemonic": "OP_46",   "length": 1, "operands": [], "desc": "字符串/比较操作", "handler": 0x10004830},
    0x47: {"mnemonic": "OP_47",   "length": 1, "operands": [], "desc": "比较操作（含 0x2c 逗号判定）", "handler": 0x10004360},

    # ── 消息/控制 ────────────────────────────────────────────────────────────
    0x29: {"mnemonic": "END",    "length": 1, "operands": [], "desc": "脚本终止检查（栈顶越界则停脚本）", "handler": 0x10004B60},
    0x2A: {"mnemonic": "FRAME",  "length": 1, "operands": [], "desc": "帧上下文保存/恢复", "handler": 0x10004C70},
    0x2B: {"mnemonic": "NOP",    "length": 1, "operands": [], "desc": "空操作（推进 IP 并跳过 0x05）", "handler": 0x10004E50},
    0x2C: {"mnemonic": "MSG",    "length": 1, "operands": [], "desc": "消息显示（传递当前 code-relative IP 给 0x10002090）", "handler": 0x10005470},
    0x2D: {"mnemonic": "LOOP",   "length": 1, "operands": [], "desc": "循环/回跳（含 0x2c 判定）", "handler": 0x10005560},
    0x2E: {"mnemonic": "OP_2E",  "length": 1, "operands": [], "desc": "栈/流程操作", "handler": 0x100051C0},
    0x2F: {"mnemonic": "OP_2F",  "length": 1, "operands": [], "desc": "栈/流程操作", "handler": 0x10005150},
    0x35: {"mnemonic": "OP_35",  "length": 1, "operands": [], "desc": "罕见（未在样本中使用）", "handler": 0x10003FE0},
    0x36: {"mnemonic": "OP_36",  "length": 1, "operands": [], "desc": "罕见（未在样本中使用）", "handler": 0x10003FE0},
    0x37: {"mnemonic": "OP_37",  "length": 1, "operands": [], "desc": "罕见（未在样本中使用）", "handler": 0x10006B30},
    0x50: {"mnemonic": "OP_50",  "length": 1, "operands": [], "desc": "消息/格式化操作", "handler": 0x10006C70},
    0x51: {"mnemonic": "OP_51",  "length": 1, "operands": [], "desc": "消息/格式化操作", "handler": 0x10006D90},
    0x52: {"mnemonic": "OP_52",  "length": 1, "operands": [], "desc": "消息/格式化操作（printf 类）", "handler": 0x10006CE0},
    0x60: {"mnemonic": "DISPATCH", "length": 1, "operands": [], "desc": "按值分发（0/1/2 三分支）", "handler": 0x10005610},
    0x61: {"mnemonic": "DISPATCH", "length": 1, "operands": [], "desc": "按值分发", "handler": 0x10005880},

    # ── 系统调用（0x80–0xFF，引擎内建 API） ─────────────────────────────────
    0x80: {"mnemonic": "SYS_80", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007240},
    0x81: {"mnemonic": "SYS_81", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007F60},
    0x82: {"mnemonic": "SYS_82", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10006E00},
    0x83: {"mnemonic": "SYS_83", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007040},
    0x84: {"mnemonic": "SYS_84", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008770},
    0x85: {"mnemonic": "SYS_85", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10006F40},
    0x86: {"mnemonic": "SYS_86", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007120},
    0x87: {"mnemonic": "SYS_87", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007120},
    0x88: {"mnemonic": "SYS_88", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008050},
    0x89: {"mnemonic": "SYS_89", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x100082D0},
    0x8A: {"mnemonic": "SYS_8A", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008340},
    0x8B: {"mnemonic": "SYS_8B", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008500},
    0x8C: {"mnemonic": "SYS_8C", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008EB0},
    0x8D: {"mnemonic": "SYS_8D", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008FF0},
    0x8E: {"mnemonic": "SYS_8E", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x100094E0},
    0x8F: {"mnemonic": "SYS_8F", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007430},
    0x90: {"mnemonic": "SYS_90", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007700},
    0x91: {"mnemonic": "SYS_91", "length": 1, "operands": [], "desc": "系统调用（高频）", "handler": 0x10007980},
    0x92: {"mnemonic": "SYS_92", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009160},
    0x93: {"mnemonic": "SYS_93", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008540},
    0x94: {"mnemonic": "SYS_94", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10007C90},
    0x95: {"mnemonic": "SYS_95", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009350},
    0xA0: {"mnemonic": "SYS_A0", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x100096B0},
    0xA1: {"mnemonic": "SYS_A1", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009760},
    0xB0: {"mnemonic": "SYS_B0", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009A10},
    0xB1: {"mnemonic": "SYS_B1", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009D10},
    0xB2: {"mnemonic": "SYS_B2", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009DD0},
    0xB3: {"mnemonic": "SYS_B3", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009ED0},
    0xC0: {"mnemonic": "SYS_C0", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009800},
    0xC1: {"mnemonic": "SYS_C1", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x100098E0},
    0xD0: {"mnemonic": "SYS_D0", "length": 1, "operands": [], "desc": "系统调用（高频）", "handler": 0x100088C0},
    0xD2: {"mnemonic": "SYS_D2", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008AB0},
    0xD3: {"mnemonic": "SYS_D3", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10008CA0},
    0xE0: {"mnemonic": "SYS_E0", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009F90},
    0xE1: {"mnemonic": "SYS_E1", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A150},
    0xE2: {"mnemonic": "SYS_E2", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A250},
    0xE3: {"mnemonic": "SYS_E3", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A330},
    0xE4: {"mnemonic": "SYS_E4", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A400},
    0xE5: {"mnemonic": "SYS_E5", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10009F90},
    0xE6: {"mnemonic": "SYS_E6", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A1D0},
    0xF0: {"mnemonic": "SYS_F0", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A770},
    0xF1: {"mnemonic": "SYS_F1", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A840},
    0xF2: {"mnemonic": "SYS_F2", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000AB20},
    0xF3: {"mnemonic": "SYS_F3", "length": 2, "operands": [{"type": "u8", "bits": 8}], "desc": "系统调用（带 u8 子码）", "handler": 0x1000AC10},
    0xF4: {"mnemonic": "SYS_F4", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A630},
    0xF5: {"mnemonic": "SYS_F5", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A6F0},
    0xF6: {"mnemonic": "SYS_F6", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A540},
    0xF7: {"mnemonic": "SYS_F7", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A600},
    0xF8: {"mnemonic": "SYS_F8", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000AAF0},
    0xFA: {"mnemonic": "SYS_FA", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A4D0},
    0xFB: {"mnemonic": "SYS_FB", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x10003FE0},
    0xFC: {"mnemonic": "SYS_FC", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000A8C0},
    0xFD: {"mnemonic": "SYS_FD", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000AA10},
    0xFE: {"mnemonic": "SYS_FE", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000AC80},
    0xFF: {"mnemonic": "SYS_FF", "length": 1, "operands": [], "desc": "系统调用", "handler": 0x1000ACA0},
}

# ─────────────────────────────────────────────────────────────────────────────
# 变长指令的长度解析
# ─────────────────────────────────────────────────────────────────────────────
def resolve_length(opcode, data, pos):
    """返回 (length, error)。pos 为 opcode 字节在文件中的偏移。
    op_33（STR）为变长：长度 <0xFF 用 1 字节，否则 u32。"""
    entry = OPCODES[opcode]
    ln = entry["length"]
    if ln == "str":
        # 需要 data 与 pos，计算字符串长度
        if pos + 1 >= len(data):
            return None, "op_33 缺长度字节"
        l0 = data[pos + 1]
        if l0 < 0xFF:
            return 2 + l0, None
        else:
            if pos + 6 > len(data):
                return None, "op_33 缺 u32 长度"
            import struct
            l = struct.unpack("<I", data[pos + 2:pos + 6])[0]
            return 6 + l, None
    return ln, None


# 便捷：直接获取定长指令长度（不含 op_33）
def fixed_length(opcode):
    ln = OPCODES[opcode]["length"]
    return None if ln == "str" else ln


# 便捷：助记符查找
def mnemonic(opcode):
    return OPCODES[opcode]["mnemonic"]


# 所有合法 opcode 集合（快速校验用）
ALL_OPCODES = frozenset(OPCODES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# 唯一助记符（用于无损 round-trip）
# 同一 mnemonic 可能对应多个 opcode（如 JMP=0x20/0x24，PUSHI=0x30/0x31），
# 反汇编时对歧义助记符追加 "_XX" 后缀，保证汇编器能精确还原字节。
# ─────────────────────────────────────────────────────────────────────────────
def _build_unique_mnemonics():
    from collections import defaultdict
    mnem_to_ops = defaultdict(list)
    for op, e in OPCODES.items():
        mnem_to_ops[e["mnemonic"]].append(op)
    unique = {}
    for op, e in OPCODES.items():
        m = e["mnemonic"]
        if len(mnem_to_ops[m]) > 1:
            unique[op] = "%s_%02X" % (m, op)
        else:
            unique[op] = m
    return unique


UNIQUE_MNEMONICS = _build_unique_mnemonics()
MNEMONIC_TO_OP = {v: k for k, v in UNIQUE_MNEMONICS.items()}


def unique_mnemonic(opcode):
    return UNIQUE_MNEMONICS[opcode]


if __name__ == "__main__":
    print("ISM opcode 总数：%d" % len(OPCODES))
    fixed = [o for o, e in OPCODES.items() if e["length"] != "str"]
    var = [o for o, e in OPCODES.items() if e["length"] == "str"]
    print("定长指令：%d 条；变长指令：%d 条（%s）" % (len(fixed), len(var), var))
