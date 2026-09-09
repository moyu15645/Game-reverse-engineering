# -*- coding: utf-8 -*-
"""
assembler.py — ISM 脚本汇编器

读取语义化汇编文本（asm.txt），重建二进制 .ISM 脚本。

用法：
    python assembler.py <input.asm.txt> [-o <output.bin>] [--encoding <codec>]
    python assembler.py <input_dir> [--encoding <codec>]   # 批量处理目录

支持拖放：把 .asm.txt 文件/文件夹拖到本脚本图标上即可。
依赖 opcodelist.py（同目录）。

重建规则：
    * 头部大小字段、code_base、入口/函数/字符串表偏移全部依据当前布局重算。
    * op_33 内联字符串按新位置重新加密（密钥随偏移变化）。
    * 跳转、switch 表、字符串引用按标签重定位。
"""

import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodelist import OPCODES, MNEMONIC_TO_OP  # noqa: E402
from disassembler import encrypt_33  # noqa: E402

DEFAULT_ENCODING = "cp932"


# ─────────────────────────────────────────────────────────────────────────────
# 字符串反解析（占位符还原为字节）
# ─────────────────────────────────────────────────────────────────────────────
def unescape_to_bytes(token, encoding=DEFAULT_ENCODING):
    """token 为含双引号的字符串字面量，返回原始 bytes。
    支持 \\\\、\\"、{{XX}} 占位符，其余按 encoding 编码。
    """
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        inner = token[1:-1]
    else:
        inner = token
    parts = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n:
            nxt = inner[i + 1]
            if nxt == "\\":
                parts.append(b"\\")
                i += 2
            elif nxt == '"':
                parts.append(b'"')
                i += 2
            else:
                parts.append(b"\\")
                i += 1
        elif c == "{" and inner[i:i + 2] == "{{":
            j = inner.find("}}", i + 2)
            if j < 0:
                raise ValueError("非法占位符：%r" % inner[i:i + 12])
            hexstr = inner[i + 2:j]
            parts.append(bytes([int(hexstr, 16)]))
            i = j + 2
        else:
            parts.append(c)
            i += 1
    out = bytearray()
    for p in parts:
        if isinstance(p, bytes):
            out.extend(p)
        else:
            out.extend(p.encode(encoding))
    return bytes(out)


# ─────────────────────────────────────────────────────────────────────────────
# 简单 tokenizer（尊重引号）
# ─────────────────────────────────────────────────────────────────────────────
def strip_comment(line):
    """去除行内注释（; 在引号外时）。"""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ";" and not in_quote:
            return line[:i]
    return line


def split_tokens(line):
    """按空白与逗号切分，但保留引号内字符串为一个 token。"""
    tokens = []
    cur = []
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
            cur.append(ch)
        elif (ch in " \t,") and not in_quote:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def parse_hex(tok):
    if tok.lower().startswith("0x"):
        return int(tok, 16)
    return int(tok, 0)


# ─────────────────────────────────────────────────────────────────────────────
# asm.txt 解析
# ─────────────────────────────────────────────────────────────────────────────
def parse_doc(text, encoding):
    doc = {
        "encoding": encoding,
        "header": {},
        "entries": [],      # (label, p1, p2)
        "funcs": [],        # (label, p1, p2, name_bytes)
        "strings_e": [],    # [bytes]
        "strings_f": [],    # [bytes]
        "labels": [],       # (label, p1, p2, name_bytes)
        "code": [],         # (label_or_None, (opcode, [operand_tokens]))
        "data": [],         # list of table dicts
    }
    section = None
    cur_table = None

    for raw in text.split("\n"):
        line = strip_comment(raw).strip()
        if not line:
            continue

        # 标签定义（loc_xxx: 或 tbl_xxx:）
        if line.endswith(":") and not line.startswith(".") and " " not in line.strip() and "\t" not in line.strip():
            label = line[:-1].strip()
            if section == "code":
                doc["code"].append((label, None))
            elif section == "data":
                cur_table = {"label": label, "marker": None, "default": None,
                             "min": None, "cases": []}
                doc["data"].append(cur_table)
            else:
                raise ValueError("标签 %s 出现在非法位置（section=%s）" % (label, section))
            continue

        if line.startswith("."):
            tokens = split_tokens(line)
            key = tokens[0]
            # 段标记（精确匹配，避免 .code 误匹配 .code_base）
            if key == ".encoding":
                doc["encoding"] = tokens[1]
            elif key in (".header", ".entries", ".functions", ".strings_e",
                         ".strings_f", ".labels", ".code", ".data"):
                section = key[1:]
            else:
                # 伪指令
                if section == "header":
                    doc["header"][key] = parse_hex(tokens[1])
                elif section == "entries" and key == ".entry":
                    doc["entries"].append((tokens[1], parse_hex(tokens[2]), parse_hex(tokens[3])))
                elif section == "functions" and key == ".func":
                    name = unescape_to_bytes(tokens[4], doc["encoding"])
                    doc["funcs"].append((tokens[1], parse_hex(tokens[2]), parse_hex(tokens[3]), name))
                elif section == "labels" and key == ".label":
                    name = unescape_to_bytes(tokens[4], doc["encoding"])
                    doc["labels"].append((tokens[1], parse_hex(tokens[2]), parse_hex(tokens[3]), name))
                elif section in ("strings_e", "strings_f") and key == ".string":
                    s = unescape_to_bytes(tokens[1], doc["encoding"])
                    if section == "strings_e":
                        doc["strings_e"].append(s)
                    else:
                        doc["strings_f"].append(s)
                elif section == "data" and key == ".marker":
                    cur_table["marker"] = parse_hex(tokens[1])
                elif section == "data" and key == ".default":
                    cur_table["default"] = tokens[1]
                elif section == "data" and key == ".min":
                    cur_table["min"] = parse_hex(tokens[1])
                elif section == "data" and key == ".case":
                    cur_table["cases"].append(("case", tokens[1]))
                elif section == "data" and key == ".pair":
                    cur_table["cases"].append(("pair", parse_hex(tokens[1]), tokens[2]))
                else:
                    raise ValueError("未知伪指令：%s（section=%s）" % (line, section))
            continue

        # 代码指令
        if section == "code":
            tokens = split_tokens(line)
            mnem = tokens[0]
            if mnem not in MNEMONIC_TO_OP:
                raise ValueError("未知助记符：%s" % mnem)
            opcode = MNEMONIC_TO_OP[mnem]
            doc["code"].append((None, (opcode, tokens[1:])))
        else:
            raise ValueError("无法解析的行：%s" % line)

    return doc


# ─────────────────────────────────────────────────────────────────────────────
# 头部大小与布局
# ─────────────────────────────────────────────────────────────────────────────
def header_size(doc):
    size = 12 + 20  # magic + 5 个 u32（file_size, code_base, count_a, unk_18, num_entries）
    size += len(doc["entries"]) * 12
    size += 12  # meta block 1
    for _, _, _, name in doc["funcs"]:
        size += len(name) + 14
    size += 12  # meta block 2
    for s in doc["strings_e"]:
        size += len(s) + 2
    for s in doc["strings_f"]:
        size += len(s) + 2
    if doc["labels"]:
        size += 2 + 4
        for _, _, _, name in doc["labels"]:
            size += len(name) + 14
    return size


def instruction_size(opcode, operand_tokens, encoding):
    """计算一条指令的编码后字节长度（用于布局）。"""
    if opcode == 0x33:  # STR
        s = unescape_to_bytes(operand_tokens[0], encoding)
        if len(s) < 0xFF:
            return 2 + len(s)
        else:
            return 6 + len(s)
    return OPCODES[opcode]["length"]


# ─────────────────────────────────────────────────────────────────────────────
# 主汇编流程
# ─────────────────────────────────────────────────────────────────────────────
def assemble(text, encoding=DEFAULT_ENCODING):
    doc = parse_doc(text, encoding)
    enc = doc["encoding"]

    code_base = header_size(doc)

    # ── 布局代码段：给每条指令分配文件偏移 ──
    laid_code = []  # (label, offset, opcode, operand_tokens)
    label_to_offset = {}
    offset = code_base
    for label, item in doc["code"]:
        if item is None:
            label_to_offset[label] = offset
            continue
        opcode, tokens = item
        laid_code.append((label, offset, opcode, tokens))
        offset += instruction_size(opcode, tokens, enc)
    code_end = offset

    # 代码段内所有标签（含指令前标签）——已在 label_to_offset 中登记

    # ── 布局数据段 ──
    laid_data = []  # (table_dict, tpos, size)
    for t in doc["data"]:
        tpos = offset
        if t["marker"] == 0xFF000000:
            size = 12 + len(t["cases"]) * 4
        elif t["marker"] == 0xFE000000:
            size = 8 + len(t["cases"]) * 8
        else:
            raise ValueError("非法 switch 表标记 0x%08X" % t["marker"])
        laid_data.append((t, tpos, size))
        label_to_offset[t["label"]] = tpos
        offset += size
    total_size = offset

    # ── 建立 switch 表 → op_0F 指令偏移 的映射 ──
    table_instr_off = {}
    for label, instr_off, opcode, tokens in laid_code:
        if opcode == 0x0F:
            table_instr_off[tokens[0]] = instr_off

    def resolve(label):
        off = label_to_offset.get(label)
        if off is None:
            raise ValueError("未解析标签：%s" % label)
        return off

    def resolve_off(token):
        """入口/函数/标签表的 off 字段：标签则解析为 code-relative 偏移，
        原始十六进制（高位置位的具名全局变量索引）则原样返回。"""
        if token.lower().startswith("0x"):
            return parse_hex(token)
        return resolve(token) - code_base

    # ── 编码头部 ──
    out = bytearray()
    out += b"ISM SCRIPT\x00\x00"
    out += struct.pack("<I", total_size)           # file_size
    out += struct.pack("<I", code_base)            # code_base
    out += struct.pack("<I", doc["header"].get(".count_a", 0))    # count_a（原样）
    out += struct.pack("<I", len(doc["entries"]))  # unk_18 (= num_entries)
    out += struct.pack("<I", len(doc["entries"]))  # num_entries

    for token, p1, p2 in doc["entries"]:
        out += struct.pack("<III", resolve_off(token), p1, p2)

    out += struct.pack("<I", len(doc["funcs"]))    # count_d
    out += struct.pack("<I", doc["header"].get(".count_1", len(doc["funcs"])))  # count_1（原样）
    out += struct.pack("<I", doc["header"].get(".count_2", 0))

    for token, p1, p2, name in doc["funcs"]:
        out += struct.pack("<III", resolve_off(token), p1, p2)
        out += bytes([len(name)])
        out += name + b"\x00"

    out += struct.pack("<I", len(doc["strings_e"]))   # unk_y0 (= count_e)
    out += struct.pack("<I", len(doc["strings_e"]))   # count_e
    out += struct.pack("<I", len(doc["strings_f"]))   # count_f

    for s in doc["strings_e"]:
        out += bytes([len(s)]) + s + b"\x00"
    for s in doc["strings_f"]:
        out += bytes([len(s)]) + s + b"\x00"

    if doc["labels"]:
        out += b"\xFF\x01"
        out += struct.pack("<I", len(doc["labels"]))
        for token, p1, p2, name in doc["labels"]:
            out += struct.pack("<III", resolve_off(token), p1, p2)
            out += bytes([len(name)])
            out += name + b"\x00"

    assert len(out) == code_base, "头部大小计算不一致：%d != %d" % (len(out), code_base)

    # ── 编码代码段 ──
    for label, instr_off, opcode, tokens in laid_code:
        out += bytes([opcode])
        if opcode == 0x33:  # STR
            s = unescape_to_bytes(tokens[0], enc)
            if len(s) < 0xFF:
                out += bytes([len(s)])
            else:
                out += b"\xFF" + struct.pack("<I", len(s))
            out += encrypt_33(s, instr_off, code_base)[0]
        elif opcode == 0x0F:  # SWITCH
            tbl_off = resolve(tokens[0])
            out += struct.pack("<I", tbl_off - code_base)
            out += struct.pack("<I", parse_hex(tokens[1]))
        elif opcode in (0x20, 0x21, 0x24):  # JMP/JZ（相对指令偏移）
            out += struct.pack("<i", resolve(tokens[0]) - instr_off)
        elif opcode == 0x45:  # STRREF（相对 code_base）
            out += struct.pack("<I", resolve(tokens[0]) - code_base)
        elif opcode == 0x32:
            out += struct.pack("<I", parse_hex(tokens[0]))
            out += struct.pack("<I", parse_hex(tokens[1]))
        elif opcode in (0x39, 0x3B, 0x3D, 0x3F):
            out += bytes([parse_hex(tokens[0])])
            out += struct.pack("<I", parse_hex(tokens[1]))
        elif opcode == 0xF3:
            out += bytes([parse_hex(tokens[0])])
        elif opcode in (0x30, 0x31, 0x23, 0x38, 0x3A, 0x3C, 0x3E):
            out += struct.pack("<I", parse_hex(tokens[0]))
        else:
            pass  # 无操作数指令

    # ── 编码数据段（switch 表） ──
    for t, tpos, size in laid_data:
        ref_off = table_instr_off.get(t["label"])
        if ref_off is None:
            raise ValueError("switch 表 %s 未被任何 SWITCH 指令引用" % t["label"])
        if t["marker"] == 0xFF000000:
            out += struct.pack("<I", 0xFF000000)
            out += struct.pack("<i", resolve(t["default"]) - ref_off)
            out += struct.pack("<I", t["min"])
            for kind, target in t["cases"]:
                out += struct.pack("<i", resolve(target) - ref_off)
        elif t["marker"] == 0xFE000000:
            out += struct.pack("<I", 0xFE000000)
            out += struct.pack("<i", resolve(t["default"]) - ref_off)
            for kind, key, target in t["cases"]:
                out += struct.pack("<I", key)
                out += struct.pack("<i", resolve(target) - ref_off)

    return bytes(out)


# ─────────────────────────────────────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────────────────────────────────────
def process_file(input_path, output_path, encoding):
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()
    data = assemble(text, encoding)
    with open(output_path, "wb") as f:
        f.write(data)
    return output_path


def main(argv):
    output = None
    encoding = DEFAULT_ENCODING
    inputs = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "--output"):
            output = argv[i + 1]
            i += 2
        elif a == "--encoding":
            encoding = argv[i + 1]
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
                if fn.lower().endswith(".asm.txt"):
                    full = os.path.join(inp, fn)
                    base = fn[:-len(".asm.txt")]
                    out = os.path.join(inp, base + ".rebuild.ism")
                    process_file(full, out, encoding)
                    print("汇编：%s -> %s" % (full, out))
        else:
            if output is None:
                base = inp
                if base.lower().endswith(".asm.txt"):
                    base = base[:-len(".asm.txt")]
                output = base + ".rebuild"
            process_file(inp, output, encoding)
            print("汇编：%s -> %s" % (inp, output))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
