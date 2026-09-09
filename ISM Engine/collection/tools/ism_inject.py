# -*- coding: utf-8 -*-
"""
ism_inject.py — 译文注入器（把日繁译文写回 .ISM 脚本）

流程：
    1. 读入原 .ISM 二进制 + _text/<name>.json（含 trans_kanji 字段）。
    2. 建立「STR 指令偏移 → 新字符串(bytes)」补丁表：
         - 内联 STR：直接以 JSON 的 offset 为 STR 偏移。
         - 共享 STRREF：解析其目标 STR（code_base + ref），改目标 STR。
    3. 反汇编为 asm，按 STR 指令顺序一一对应地替换字符串字面量。
    4. 交给 assembler 重汇编（自动变长重布局 + 按新位置重加密）。

用法：
    python ism_inject.py <dir>            # 注入目录下所有 .ISM（查 _text/<name>.json）
    python ism_inject.py <file.ism>       # 单文件注入

依赖 opcodelist.py、disassembler.py、assembler.py（同目录）。
"""

import os
import sys
import json
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disassembler import (  # noqa: E402
    parse_header, sweep_code, disassemble, escape_bytes)
from assembler import assemble  # noqa: E402

DEFAULT_ENCODING = "cp932"


def build_patch_map(data, records, encoding=DEFAULT_ENCODING):
    """返回 {str_file_offset: bytes} 补丁表。"""
    header = parse_header(data)
    code_base = header["code_base"]
    instructions, tables, unknown = sweep_code(data, code_base)

    str_set = {fo for fo, op in instructions if op == 0x33}
    ref_target = {fo: code_base + struct.unpack("<I", data[fo + 1:fo + 5])[0]
                  for fo, op in instructions if op == 0x45}

    patch = {}
    skipped = 0
    for r in records:
        tk = r.get("trans_kanji")
        if tk is None:
            skipped += 1
            continue
        off = int(r["offset"], 16)
        if off in str_set:
            target = off
        elif off in ref_target:
            target = ref_target[off]
        else:
            raise ValueError("无法定位台词 offset %s" % r["offset"])

        new_bytes = tk.encode(encoding)
        # 共享目标：不同译文则报错（理论上去重后不会发生）
        if target in patch and patch[target] != new_bytes:
            raise ValueError(
                "共享字符串 0x%X 出现不同译文：%r vs %r"
                % (target, patch[target].decode(encoding, "replace"),
                   new_bytes.decode(encoding, "replace")))
        patch[target] = new_bytes

    # 说话人标签（name 池）：从 records 的 name_offset 读，按缓存里的 name_zh_bytes_hex 替换
    name_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "_work", "translation_cache.json")
    name_cache = {}
    if os.path.exists(name_cache_path):
        try:
            name_cache = json.load(open(name_cache_path, encoding="utf-8"))
        except Exception:
            name_cache = {}
    name_patched = 0
    name_skipped = 0
    seen_name_targets = set()
    for r in records:
        no = r.get("name_offset") or ""
        if not no:
            continue
        try:
            noff = int(no, 16)
        except ValueError:
            continue
        if noff not in str_set:
            # name_offset 指向 STRREF 目标（name 池 STR），必须也是 STR
            name_skipped += 1
            continue
        ent = name_cache.get(no)
        if not ent or "name_zh_bytes_hex" not in ent:
            name_skipped += 1
            continue
        new_bytes = bytes.fromhex(ent["name_zh_bytes_hex"])
        if noff in patch and patch[noff] != new_bytes:
            # name 池与台词 patch 冲突（极少见，同一 STR 被两边引用），跳过 name patch
            print("  [warn] name 池 0x%X 与台词 patch 冲突，已跳过 (%r)"
                  % (noff, r.get("name", "")))
            name_skipped += 1
            continue
        patch[noff] = new_bytes
        seen_name_targets.add(noff)
        name_patched += 1
    return patch, skipped, name_patched, name_skipped


def inject(data, patch, encoding=DEFAULT_ENCODING):
    """把补丁表写入 asm 并重汇编，返回新二进制。"""
    if not patch:
        return data

    header = parse_header(data)
    code_base = header["code_base"]
    instructions, tables, unknown = sweep_code(data, code_base)

    # STR 指令偏移（按代码扫描顺序）与 asm 中 STR 行（按输出顺序）一一对应
    str_offsets = [fo for fo, op in instructions if op == 0x33]
    lines = disassemble(data, encoding)
    str_line_idx = [i for i, l in enumerate(lines) if l.strip().startswith("STR ")]
    if len(str_offsets) != len(str_line_idx):
        raise ValueError("STR 指令数与 asm STR 行数不一致：%d vs %d"
                         % (len(str_offsets), len(str_line_idx)))

    off_to_line = {off: str_line_idx[k] for k, off in enumerate(str_offsets)}

    for off, new_bytes in patch.items():
        if off not in off_to_line:
            raise ValueError("补丁目标 0x%X 不是 STR 指令" % off)
        li = off_to_line[off]
        lines[li] = "    STR %s" % escape_bytes(new_bytes, encoding)

    return assemble("\n".join(lines) + "\n", encoding)


def process_one(ism_path, json_path, out_path=None, encoding=DEFAULT_ENCODING):
    with open(ism_path, "rb") as f:
        data = f.read()
    records = json.load(open(json_path, encoding="utf-8"))
    patch, skipped, name_patched, name_skipped = build_patch_map(data, records, encoding)
    new_data = inject(data, patch, encoding)

    if out_path is None:
        out_path = ism_path
    with open(out_path, "wb") as f:
        f.write(new_data)

    n_orig = len(data)
    n_new = len(new_data)
    print("%s：补丁 %d 处（台词 %d 跳过 %d，name %d 跳过 %d），大小 %d -> %d (%+d)"
          % (os.path.basename(ism_path), len(patch),
             len(patch) - name_patched, skipped, name_patched, name_skipped,
             n_orig, n_new, n_new - n_orig))
    return new_data


def main(argv):
    inputs = [a for a in argv if not a.startswith("-")]
    if not inputs:
        print(__doc__)
        return 1
    for inp in inputs:
        if os.path.isdir(inp):
            for fn in sorted(os.listdir(inp)):
                if not fn.lower().endswith(".ism"):
                    continue
                name = os.path.splitext(fn)[0]
                jp = os.path.join("_text", name + ".json")
                if not os.path.exists(jp):
                    continue
                process_one(os.path.join(inp, fn), jp)
        else:
            name = os.path.splitext(os.path.basename(inp))[0]
            jp = os.path.join("_text", name + ".json")
            if not os.path.exists(jp):
                print("缺少译文：%s" % jp)
                continue
            process_one(inp, jp)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
