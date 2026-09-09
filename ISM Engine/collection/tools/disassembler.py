# -*- coding: utf-8 -*-
"""
disassembler.py — ISM 脚本反汇编器

读取二进制 .ISM 脚本，生成语义化汇编文本（asm.txt）。

用法：
    python disassembler.py <input.ism> [-o <output.asm.txt>] [--encoding <codec>]
    python disassembler.py <input_dir> [--encoding <codec>]   # 批量处理目录

支持拖放：把 .ISM 文件/文件夹拖到本脚本图标上即可。
依赖 opcodelist.py（同目录）。
"""

import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodelist import OPCODES, resolve_length, unique_mnemonic  # noqa: E402

DEFAULT_ENCODING = "cp932"


# ─────────────────────────────────────────────────────────────────────────────
# 字符串转义（语义化、抗乱码）
# ─────────────────────────────────────────────────────────────────────────────
def escape_bytes(b, encoding=DEFAULT_ENCODING):
    """把原始字节串 b 转为 asm 字符串字面量（含双引号）。
    可显示字符直接输出；控制字节与不安全字节用 {{XX}} 占位；CR/LF 用 {{0D}}/{{0A}}。
    """
    try:
        s = b.decode(encoding)
        parts = []
        for ch in s:
            if ch == "\\":
                parts.append("\\\\")
            elif ch == '"':
                parts.append('\\"')
            elif ch == "\r":
                parts.append("{{0D}}")
            elif ch == "\n":
                parts.append("{{0A}}")
            elif ord(ch) < 0x20:
                parts.append("{{%02X}}" % ord(ch))
            else:
                parts.append(ch)
        return '"' + "".join(parts) + '"'
    except UnicodeDecodeError:
        # 逐字节回退：多字节序列按 cp932 尝试，失败则占位符
        parts = []
        i = 0
        while i < len(b):
            c = b[i]
            if 0x20 <= c < 0x7F:
                if c == 0x5C:
                    parts.append("\\\\")
                elif c == 0x22:
                    parts.append('\\"')
                else:
                    parts.append(chr(c))
                i += 1
            elif c == 0x0D:
                parts.append("{{0D}}")
                i += 1
            elif c == 0x0A:
                parts.append("{{0A}}")
                i += 1
            elif c == 0x00:
                parts.append("{{00}}")
                i += 1
            elif i + 1 < len(b) and ((0x81 <= c <= 0x9F) or (0xE0 <= c <= 0xEF)):
                two = b[i:i + 2]
                try:
                    parts.append(two.decode(encoding))
                    i += 2
                    continue
                except UnicodeDecodeError:
                    pass
                parts.append("{{%02X}}" % c)
                i += 1
            else:
                parts.append("{{%02X}}" % c)
                i += 1
        return '"' + "".join(parts) + '"'


# ─────────────────────────────────────────────────────────────────────────────
# ISM 头部解析
# ─────────────────────────────────────────────────────────────────────────────
def parse_header(data):
    """解析 ISM 头部，返回 dict。所有字段与 vm_analysis.md 一致。"""
    if data[:12] != b"ISM SCRIPT\x00\x00":
        raise ValueError("非 ISM 脚本（magic 不符）：%r" % data[:12])
    r = {}
    r["file_size"] = struct.unpack("<I", data[0x0C:0x10])[0]
    r["code_base"] = struct.unpack("<I", data[0x10:0x14])[0]
    r["count_a"] = struct.unpack("<I", data[0x14:0x18])[0]
    r["unk_18"] = struct.unpack("<I", data[0x18:0x1C])[0]
    r["num_entries"] = struct.unpack("<I", data[0x1C:0x20])[0]

    pos = 0x20
    entries = []
    for _ in range(r["num_entries"]):
        off, p1, p2 = struct.unpack("<III", data[pos:pos + 12])
        entries.append((off, p1, p2))
        pos += 12
    r["entries"] = entries

    count_d, count_1, count_2 = struct.unpack("<III", data[pos:pos + 12])
    pos += 12
    r["count_d"] = count_d
    r["count_1"] = count_1
    r["count_2"] = count_2

    funcs = []
    for _ in range(count_d):
        off, p1, p2 = struct.unpack("<III", data[pos:pos + 12])
        ln = data[pos + 12]
        name = data[pos + 13:pos + 13 + ln]
        funcs.append((off, p1, p2, name))
        pos += ln + 14
    r["funcs"] = funcs

    unk_y0, count_e, count_f = struct.unpack("<III", data[pos:pos + 12])
    pos += 12
    r["unk_y0"] = unk_y0
    r["count_e"] = count_e
    r["count_f"] = count_f

    strings_e = []
    for _ in range(count_e):
        ln = data[pos]
        strings_e.append(data[pos + 1:pos + 1 + ln])
        pos += ln + 2
    r["strings_e"] = strings_e

    strings_f = []
    for _ in range(count_f):
        ln = data[pos]
        strings_f.append(data[pos + 1:pos + 1 + ln])
        pos += ln + 2
    r["strings_f"] = strings_f

    # 可选 FF 01 标记的 label table
    labels = []
    if pos + 1 < len(data) and data[pos] == 0xFF and data[pos + 1] == 0x01:
        pos += 2
        lcount = struct.unpack("<I", data[pos:pos + 4])[0]
        pos += 4
        for _ in range(lcount):
            off, p1, p2 = struct.unpack("<III", data[pos:pos + 12])
            ln = data[pos + 12]
            name = data[pos + 13:pos + 13 + ln]
            labels.append((off, p1, p2, name))
            pos += ln + 14
    r["labels"] = labels
    r["header_end"] = pos
    return r


# ─────────────────────────────────────────────────────────────────────────────
# op_33 字符串解密
# ─────────────────────────────────────────────────────────────────────────────
def decrypt_33(data, opcode_pos, code_base):
    """解密 op_33 内联字符串。
    密钥 = (opcode_pos - code_base) & 0xFF，若为 0xFF 则归零。
    明文字节 = (~密文) & 0xFF ^ 密钥。
    返回 (明文 bytes, 密钥 int)。
    """
    ln = data[opcode_pos + 1]
    if ln < 0xFF:
        length = ln
        data_off = opcode_pos + 2
    else:
        length = struct.unpack("<I", data[opcode_pos + 2:opcode_pos + 6])[0]
        data_off = opcode_pos + 6
    key = (opcode_pos - code_base) & 0xFF
    if key == 0xFF:
        key = 0
    enc = data[data_off:data_off + length]
    plain = bytes([((~b) & 0xFF) ^ key for b in enc])
    return plain, key


def encrypt_33(plain, opcode_pos, code_base):
    """加密（汇编器用）：密文 = (~明文) & 0xFF ^ 密钥。"""
    key = (opcode_pos - code_base) & 0xFF
    if key == 0xFF:
        key = 0
    return bytes([((~b) & 0xFF) ^ key for b in plain]), key


# ─────────────────────────────────────────────────────────────────────────────
# 代码线性扫描
# ─────────────────────────────────────────────────────────────────────────────
def sweep_code(data, code_base):
    """从 code_base 线性扫描代码，直到遇到未知 opcode、文件尾，或数据区起点。
    代码区是连续的 [code_base, code_end)，code_end = 首个 switch 表偏移。
    switch 表位于文件末尾的独立数据区，一旦扫描进入数据区就必须停止，
    否则会把表字节误当成 opcode（产生垃圾标签 / 缓冲区越界）。

    收集：指令列表、op_0F 的 switch 表引用。
    返回 (instructions, tables, unknown_pos)。
    instructions: [(file_off, opcode)]
    tables: [(table_off, count, instr_off)]  instr_off 为 op_0F 指令偏移
    unknown_pos: 首个未知 opcode 的文件偏移，无则 None
    """
    pos = code_base
    instructions = []
    tables = []
    unknown = None
    end = len(data)
    min_table_off = None
    while pos < end:
        # 一旦知道了数据区起点（首个 switch 表），扫描到此为止
        if min_table_off is not None and pos >= code_base + min_table_off:
            break
        op = data[pos]
        if op not in OPCODES:
            unknown = pos
            break
        instructions.append((pos, op))
        if op == 0x0F:
            table_off = struct.unpack("<I", data[pos + 1:pos + 5])[0]
            count = struct.unpack("<I", data[pos + 5:pos + 9])[0]
            tables.append((table_off, count, pos))
            if min_table_off is None or table_off < min_table_off:
                min_table_off = table_off
        ln, err = resolve_length(op, data, pos)
        if err:
            unknown = pos
            break
        pos += ln
    return instructions, tables, unknown


# ─────────────────────────────────────────────────────────────────────────────
# switch 表解析
# ─────────────────────────────────────────────────────────────────────────────
def decode_switch_table(data, code_base, table_off, count, instr_off):
    """解析 switch 表。返回 dict。
    range 表: [marker=0xff000000][default_off][min][case_off * count]，大小 = 12 + count*4
    sparse 表: [marker=0xfe000000][default_off][(key, off) * (count-1)]，大小 = count*8
       —— 注意：sparse 表的「pair 0」位置被 marker/default 占用，
          真正的 (key, off) 对从下标 1 到 count-1，共 count-1 个。
          VM 里二分查找范围正是 [1, count-1]（见 ism.dll 0x10005417）。
    跳转偏移基准 = instr_off（op_0F 指令偏移）。
    """
    tpos = code_base + table_off
    marker = struct.unpack("<I", data[tpos:tpos + 4])[0]
    if marker == 0xFF000000:
        default_off = struct.unpack("<i", data[tpos + 4:tpos + 8])[0]
        minv = struct.unpack("<I", data[tpos + 8:tpos + 12])[0]
        cases = []
        for i in range(count):
            off = struct.unpack("<i", data[tpos + 12 + i * 4:tpos + 16 + i * 4])[0]
            cases.append((minv + i, instr_off + off))
        size = 12 + count * 4
        return {"kind": "range", "default": instr_off + default_off,
                "min": minv, "cases": cases, "size": size}
    elif marker == 0xFE000000:
        default_off = struct.unpack("<i", data[tpos + 4:tpos + 8])[0]
        cases = []
        for i in range(1, count):
            key = struct.unpack("<I", data[tpos + i * 8:tpos + i * 8 + 4])[0]
            off = struct.unpack("<i", data[tpos + i * 8 + 4:tpos + i * 8 + 8])[0]
            cases.append((key, instr_off + off))
        size = count * 8
        return {"kind": "sparse", "default": instr_off + default_off,
                "min": None, "cases": cases, "size": size}
    else:
        raise ValueError("未知 switch 表标记 0x%08X @0x%X" % (marker, tpos))


# ─────────────────────────────────────────────────────────────────────────────
# 反汇编主流程
# ─────────────────────────────────────────────────────────────────────────────
def disassemble(data, encoding=DEFAULT_ENCODING):
    header = parse_header(data)
    code_base = header["code_base"]

    # 头部解析完整性校验：header_end 必须精确等于 code_base（本格式不变量）
    if header["header_end"] != code_base:
        raise ValueError("头部解析不一致：header_end=0x%X != code_base=0x%X"
                         % (header["header_end"], code_base))

    instructions, tables, unknown = sweep_code(data, code_base)

    # 确定代码区终点 = 首个 switch 表偏移（若有）
    if tables:
        code_end = min(code_base + t[0] for t in tables)
        if unknown is not None and unknown < code_end:
            raise ValueError("代码区 0x%X 处出现未知 opcode 0x%02X" % (unknown, data[unknown]))
    else:
        if unknown is not None:
            raise ValueError("代码区 0x%X 处出现未知 opcode 0x%02X" % (unknown, data[unknown]))
        code_end = len(data)

    # 收集需要打标签的代码位置（file offset）
    label_offsets = set()

    # 入口 / 函数入口 / 标签入口（off 为 code-relative；高位置位(>=0x80000000)
    # 表示「具名全局变量」而非代码偏移，不作为代码标签）
    for off, _, _ in header["entries"]:
        if off < 0x80000000:
            label_offsets.add(code_base + off)
    for off, _, _, _ in header["funcs"]:
        if off < 0x80000000:
            label_offsets.add(code_base + off)
    for off, _, _, _ in header["labels"]:
        if off < 0x80000000:
            label_offsets.add(code_base + off)

    # 解码 switch 表
    decoded_tables = []
    for table_off, count, instr_off in tables:
        t = decode_switch_table(data, code_base, table_off, count, instr_off)
        t["table_off"] = table_off
        t["count"] = count
        t["table_pos"] = code_base + table_off
        decoded_tables.append(t)
        # 表目标也要打标签
        label_offsets.add(t["default"])
        for _, target in t["cases"]:
            label_offsets.add(target)

    # 收集指令中的跳转目标 / 字符串引用目标
    for file_off, op in instructions:
        if op in (0x20, 0x21, 0x24):
            rel = struct.unpack("<i", data[file_off + 1:file_off + 5])[0]
            label_offsets.add(file_off + rel)
        elif op == 0x45:
            ref = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            label_offsets.add(code_base + ref)

    # 只保留 [code_base, code_end] 内的标签。
    # 允许 code_end：某些脚本用「跳转到 code_end」作为终止惯用法（跳转到脚本末尾/数据区起点）。
    label_offsets = {o for o in label_offsets if code_base <= o <= code_end}
    label_offsets.add(code_base)  # 代码起点必有标签

    # 生成标签名
    def loc_name(off):
        return "loc_%08X" % off

    def tbl_name(off):
        return "tbl_%08X" % off

    def off_ref(off):
        """入口/函数/标签表的 off 字段转 asm 引用。
        高位置位(>=0x80000000)表示具名全局变量索引，直接输出原始十六进制；
        否则是 code-relative 代码偏移，输出代码标签。
        """
        if off < 0x80000000:
            return loc_name(code_base + off)
        return "0x%08X" % off

    lines = []
    W = lines.append

    # ── 文件头 ──
    W("; ISM 脚本反汇编")
    W("; 引擎：ISM Script VM（ism.dll）")
    W(".encoding %s" % encoding)
    W("")

    # ── 头部 ──
    W("; ===================== 头部 =====================")
    W(".header")
    W("    .file_size %d" % header["file_size"])
    W("    .code_base %d" % header["code_base"])
    W("    .count_a %d" % header["count_a"])
    W("    .unk_18 %d" % header["unk_18"])
    W("    .num_entries %d" % header["num_entries"])
    W("    .count_1 %d" % header["count_1"])
    W("    .count_2 %d" % header["count_2"])
    W("    .unk_y0 %d" % header["unk_y0"])
    W("    .count_e %d" % header["count_e"])
    W("    .count_f %d" % header["count_f"])
    W("")

    # ── 入口表 ──
    W("; ===================== 入口表 =====================")
    W(".entries")
    for off, p1, p2 in header["entries"]:
        W("    .entry %s, 0x%08X, 0x%08X" % (off_ref(off), p1, p2))
    W("")

    # ── 函数表 ──
    W("; ===================== 函数表 =====================")
    W(".functions")
    for off, p1, p2, name in header["funcs"]:
        W("    .func %s, 0x%08X, 0x%08X, %s" % (off_ref(off), p1, p2, escape_bytes(name, encoding)))
    W("")

    # ── 字符串表 e ──
    W("; ===================== 字符串表 e（外部函数名） =====================")
    W(".strings_e")
    for s in header["strings_e"]:
        W("    .string %s" % escape_bytes(s, encoding))
    W("")

    # ── 字符串表 f ──
    W("; ===================== 字符串表 f =====================")
    W(".strings_f")
    for s in header["strings_f"]:
        W("    .string %s" % escape_bytes(s, encoding))
    W("")

    # ── label table（若存在） ──
    if header["labels"]:
        W("; ===================== 标签表 =====================")
        W(".labels")
        for off, p1, p2, name in header["labels"]:
            W("    .label %s, 0x%08X, 0x%08X, %s" % (off_ref(off), p1, p2, escape_bytes(name, encoding)))
        W("")

    # ── 代码段 ──
    W("; ===================== 代码段 =====================")
    W(".code")
    W("")
    # 构建指令偏移 → 指令索引映射（用于判断标签是否落在指令边界）
    instr_offsets = {off for off, _ in instructions}
    for file_off, op in instructions:
        if file_off in label_offsets:
            W("")
            W("%s:" % loc_name(file_off))
        mnem = unique_mnemonic(op)
        if op == 0x33:
            plain, key = decrypt_33(data, file_off, code_base)
            W("    STR %s" % escape_bytes(plain, encoding))
        elif op == 0x0F:
            table_off = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            count = struct.unpack("<I", data[file_off + 5:file_off + 9])[0]
            W("    SWITCH %s, %d" % (tbl_name(code_base + table_off), count))
        elif op in (0x20, 0x21, 0x24):
            rel = struct.unpack("<i", data[file_off + 1:file_off + 5])[0]
            W("    %s %s" % (mnem, loc_name(file_off + rel)))
        elif op == 0x45:
            ref = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            W("    STRREF %s" % loc_name(code_base + ref))
        elif op == 0x30 or op == 0x31:
            imm = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            W("    %s 0x%08X" % (mnem, imm))
        elif op in (0x23, 0x38, 0x3A, 0x3C, 0x3E):
            imm = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            W("    %s 0x%08X" % (mnem, imm))
        elif op in (0x39, 0x3B, 0x3D, 0x3F):
            u8 = data[file_off + 1]
            imm = struct.unpack("<I", data[file_off + 2:file_off + 6])[0]
            W("    %s 0x%02X, 0x%08X" % (mnem, u8, imm))
        elif op == 0xF3:
            u8 = data[file_off + 1]
            W("    SYS_F3 0x%02X" % u8)
        elif op == 0x32:
            a = struct.unpack("<I", data[file_off + 1:file_off + 5])[0]
            b = struct.unpack("<I", data[file_off + 5:file_off + 9])[0]
            W("    OP_32 0x%08X, 0x%08X" % (a, b))
        else:
            W("    %s" % mnem)

    # 代码区末尾的标签（「跳转到脚本末尾」终止惯用法，目标 = code_end）
    if code_end in label_offsets:
        W("")
        W("%s:" % loc_name(code_end))

    # ── 数据段 ──
    if decoded_tables:
        W("")
        W("; ===================== 数据段（switch 表） =====================")
        W(".data")
        for t in decoded_tables:
            W("")
            W("%s:" % tbl_name(t["table_pos"]))
            if t["kind"] == "range":
                W("    .marker 0xFF000000")
                W("    .default %s" % loc_name(t["default"]))
                W("    .min 0x%08X" % t["min"])
                for value, target in t["cases"]:
                    W("    .case %s" % loc_name(target))
            else:
                W("    .marker 0xFE000000")
                W("    .default %s" % loc_name(t["default"]))
                for key, target in t["cases"]:
                    W("    .pair 0x%08X, %s" % (key, loc_name(target)))

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────────────────────────────────────
def process_file(input_path, output_path, encoding):
    with open(input_path, "rb") as f:
        data = f.read()
    lines = disassemble(data, encoding)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return output_path


def main(argv):
    args = [a for a in argv]
    output = None
    encoding = DEFAULT_ENCODING
    inputs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--output"):
            output = args[i + 1]
            i += 2
        elif a == "--encoding":
            encoding = args[i + 1]
            i += 2
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            inputs.append(a)
            i += 1

    if not inputs:
        print(__doc__)
        return 1

    for inp in inputs:
        if os.path.isdir(inp):
            for fn in sorted(os.listdir(inp)):
                if fn.lower().endswith(".ism"):
                    full = os.path.join(inp, fn)
                    out = os.path.join(inp, os.path.splitext(fn)[0] + ".asm.txt")
                    process_file(full, out, encoding)
                    print("反汇编：%s -> %s" % (full, out))
        else:
            if output is None:
                base = os.path.splitext(inp)[0]
                output = base + ".asm.txt"
            process_file(inp, output, encoding)
            print("反汇编：%s -> %s" % (inp, output))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
