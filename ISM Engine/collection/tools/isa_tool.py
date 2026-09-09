# -*- coding: utf-8 -*-
"""
isa_tool.py — ISM ARCHIVED 封包工具（《タイムカプセル “春”》data.isa）

用法：
    python isa_tool.py list    <data.isa>                  # 列出包内文件
    python isa_tool.py unpack  <data.isa>  <out_dir>       # 解包到目录（生成 _order.txt）
    python isa_tool.py pack    <dir>       <out.isa>       # 从目录封包（读 _order.txt）
    python isa_tool.py verify  <data.isa>                  # 解包→封包→SHA 比对（bit-perfect）

支持拖放：把 data.isa 拖到本脚本图标上，默认执行 unpack。

────────────────────────────────────────────────────────────────────
封包格式（已逆向实证）：
    头部 16 字节：magic "ISM ARCHIVED"(12B) + count u16 + flags u16
    索引区：count 条 entry，每条 32 字节：
        name[16] + offset u32 + size u32 + r2 u32 + r3 u32
    数据区：从索引区结束处开始（= 0x10 + count*32）

文件名拼接规则（关键）：
    name 字段前 12 字节 = 文件名前 12 字符；
    name[13] == 0x01 时，文件名延续到 r2、r3（各 4 字节，小端，NUL 截断）。
    例："TC_SCN01_1.ISM" 存为 name="TC_SCN01_1.I" + r2="SM"。
    这解释了为何磁盘上剧情脚本扩展名是 ".I" 而非 ".ISM"。
"""

import os
import sys
import json
import struct
import hashlib
import shutil

MAGIC = b"ISM ARCHIVED"
ENTRY_SIZE = 32
HEADER_SIZE = 16


# ─────────────────────────────────────────────────────────────────────────────
# 索引解析
# ─────────────────────────────────────────────────────────────────────────────
def decode_name(raw16, r2, r3):
    """把 entry 的 name[16] + r2 + r3 拼成完整文件名（bytes）。"""
    base = raw16[:12].rstrip(b"\x00")
    if raw16[13] == 0x01:  # 有延续（文件名 > 12 字符）
        ext = struct.pack("<I", r2) + struct.pack("<I", r3)
        return base + ext.rstrip(b"\x00")
    return base


def parse_index(data):
    """解析封包头部与索引，返回 (count, flags, entries)。
    entries[i] = dict(raw16, offset, size, r2, r3, name(bytes), name_str)
    """
    if data[:12] != MAGIC:
        raise ValueError("非 ISA 封包（magic 不符）：%r" % data[:12])
    count = struct.unpack("<H", data[0x0C:0x0E])[0]
    flags = struct.unpack("<H", data[0x0E:0x10])[0]

    entries = []
    pos = HEADER_SIZE
    for i in range(count):
        raw = data[pos:pos + ENTRY_SIZE]
        raw16 = raw[:16]
        offset, size, r2, r3 = struct.unpack("<IIII", raw[16:32])
        name = decode_name(raw16, r2, r3)
        entries.append({
            "raw16": raw16,
            "offset": offset,
            "size": size,
            "r2": r2,
            "r3": r3,
            "name": name,
            "name_str": name.decode("cp932", errors="replace"),
        })
        pos += ENTRY_SIZE
    return count, flags, entries


# ─────────────────────────────────────────────────────────────────────────────
# list
# ─────────────────────────────────────────────────────────────────────────────
def cmd_list(path):
    with open(path, "rb") as f:
        data = f.read()
    count, flags, entries = parse_index(data)
    print("封包：%s" % path)
    print("文件数：%d  flags：0x%04X  总大小：%d 字节" % (count, flags, len(data)))
    print("-" * 72)
    for i, e in enumerate(entries):
        print("%4d  %-28s  off=0x%08X  size=%d" % (i, e["name_str"], e["offset"], e["size"]))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# unpack
# ─────────────────────────────────────────────────────────────────────────────
def cmd_unpack(path, out_dir):
    with open(path, "rb") as f:
        data = f.read()
    count, flags, entries = parse_index(data)

    os.makedirs(out_dir, exist_ok=True)

    # 记录文件顺序（_order.txt）
    order_lines = []
    # 记录原始索引信息（封包用 manifest）
    manifest = {"count": count, "flags": flags, "entries": []}

    for i, e in enumerate(entries):
        chunk = data[e["offset"]:e["offset"] + e["size"]]
        if len(chunk) != e["size"]:
            print("警告：文件 %s 数据越界（缺 %d 字节），跳过" % (e["name_str"], e["size"] - len(chunk)))
            continue
        # 文件名去路径分隔符，避免目录穿越
        safe_name = e["name_str"].replace("/", "_").replace("\\", "_")
        out_path = os.path.join(out_dir, safe_name)
        with open(out_path, "wb") as f:
            f.write(chunk)
        order_lines.append(safe_name)
        manifest["entries"].append({
            "name_raw16": e["raw16"].hex(),
            "offset": e["offset"],
            "size": e["size"],
            "r2": e["r2"],
            "r3": e["r3"],
        })

    with open(os.path.join(out_dir, "_order.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(order_lines) + ("\n" if order_lines else ""))
    with open(os.path.join(out_dir, "_isa_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # 统计
    ext_count = {}
    ism_files = []
    for e in entries:
        ext = e["name_str"].split(".")[-1].upper() if "." in e["name_str"] else "(none)"
        ext_count[ext] = ext_count.get(ext, 0) + 1
        if ext == "ISM":
            ism_files.append(e["name_str"])

    print("解包完成：%d 个文件 -> %s" % (count, out_dir))
    print("扩展名分布：%s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(ext_count.items(), key=lambda x: -x[1])))
    print("其中 .ISM 脚本 %d 个：" % len(ism_files))
    for fn in ism_files:
        print("    %s" % fn)
    print("已生成 _order.txt（文件顺序）和 _isa_manifest.json（原始索引）")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# pack
# ─────────────────────────────────────────────────────────────────────────────
def cmd_pack(in_dir, out_path):
    order_path = os.path.join(in_dir, "_order.txt")
    manifest_path = os.path.join(in_dir, "_isa_manifest.json")
    if not os.path.exists(order_path):
        raise SystemExit("缺少 _order.txt，无法确定文件顺序")
    if not os.path.exists(manifest_path):
        raise SystemExit("缺少 _isa_manifest.json，无法还原索引")

    with open(order_path, "r", encoding="utf-8") as f:
        order = [ln.rstrip("\n") for ln in f if ln.strip()]
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    count = manifest["count"]
    flags = manifest["flags"]
    m_entries = manifest["entries"]

    # 读入所有文件内容，判断哪些文件发生变化（size 是否与原始一致）
    file_data = {}
    for name in order:
        fp = os.path.join(in_dir, name)
        if not os.path.exists(fp):
            raise SystemExit("缺少文件：%s" % name)
        with open(fp, "rb") as f:
            file_data[name] = f.read()

    # 计算每个文件的新 offset：
    #   - 若 size 未变，沿用原始 offset（保证未改动部分 bit-perfect）
    #   - 若 size 变化，重新顺序排列（追加到数据区末尾）
    # 这里采用「顺序重排」策略：变长文件会打乱物理顺序，但索引 offset 正确即可。
    # 为最大程度 bit-perfect，未变文件保持原 offset，变文件放到末尾连续区。
    unchanged = []
    changed = []
    for name in order:
        orig_size = next((me["size"] for me in m_entries if me["name_raw16"] and _m_name(me) == name), None)
        if orig_size is None:
            raise SystemExit("manifest 中找不到文件：%s" % name)
        if len(file_data[name]) == orig_size:
            unchanged.append(name)
        else:
            changed.append(name)

    # 数据区起点
    data_start = HEADER_SIZE + count * ENTRY_SIZE

    # 未变文件：保持原 offset
    new_offsets = {}
    for me in m_entries:
        nm = _m_name(me)
        if nm in unchanged:
            new_offsets[nm] = me["offset"]

    # 变文件：从数据区末尾（最大 offset+size）开始顺序排
    if changed:
        cursor = max((me["offset"] + me["size"] for me in m_entries), default=data_start)
        for nm in changed:
            new_offsets[nm] = cursor
            cursor += len(file_data[nm])

    # 组装输出
    out = bytearray()
    out += MAGIC
    out += struct.pack("<H", count)
    out += struct.pack("<H", flags)

    # 索引区：按 manifest 原顺序，更新 offset/size
    index_entries = []
    for me in m_entries:
        nm = _m_name(me)
        raw16 = bytes.fromhex(me["name_raw16"])
        new_off = new_offsets[nm]
        new_size = len(file_data[nm])
        index_entries.append((raw16, new_off, new_size, me["r2"], me["r3"]))

    for raw16, off, size, r2, r3 in index_entries:
        out += raw16 + struct.pack("<IIII", off, size, r2, r3)

    # 数据区：按 offset 排序写入
    # （未变文件保持原物理位置，变文件在末尾）
    data_map = {}
    for me in m_entries:
        nm = _m_name(me)
        data_map[new_offsets[nm]] = file_data[nm]

    # 先写原封包的数据区布局（未变文件），再写变文件
    # 为 bit-perfect：未变文件按原 offset 写入，中间空隙补 0
    cursor = data_start
    sorted_data = sorted(data_map.items(), key=lambda x: x[0])
    for off, payload in sorted_data:
        if off < cursor:
            raise SystemExit("offset 重叠，封包逻辑错误")
        if off > cursor:
            out += b"\x00" * (off - cursor)
        out += payload
        cursor = off + len(payload)

    with open(out_path, "wb") as f:
        f.write(bytes(out))

    print("封包完成：%s（%d 文件，%d 字节）" % (out_path, count, len(out)))
    if changed:
        print("变长文件 %d 个：%s" % (len(changed), ", ".join(changed)))
    return 0


def _m_name(me):
    return decode_name(bytes.fromhex(me["name_raw16"]), me["r2"], me["r3"]).decode("cp932", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# verify
# ─────────────────────────────────────────────────────────────────────────────
def cmd_verify(path):
    import tempfile
    with open(path, "rb") as f:
        orig = f.read()
    tmp = tempfile.mkdtemp(prefix="isa_verify_")
    try:
        cmd_unpack(path, tmp)
        packed = os.path.join(tmp, "_packed.isa")
        cmd_pack(tmp, packed)
        with open(packed, "rb") as f:
            rebuilt = f.read()
        a = hashlib.sha256(orig).hexdigest()
        b = hashlib.sha256(rebuilt).hexdigest()
        ok = a == b
        print("verify：%s" % ("BIT-PERFECT（一致）" if ok else "不一致"))
        print("  原始   SHA256 %s (%d 字节)" % (a, len(orig)))
        print("  重建   SHA256 %s (%d 字节)" % (b, len(rebuilt)))
        if not ok:
            n = min(len(orig), len(rebuilt))
            first = next((i for i in range(n) if orig[i] != rebuilt[i]), n)
            print("  首个差异 @0x%X" % first)
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main(argv):
    if not argv:
        print(__doc__)
        return 1

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "list":
        if len(rest) < 1:
            print("用法：python isa_tool.py list <data.isa>")
            return 1
        return cmd_list(rest[0])

    elif cmd == "unpack":
        if len(rest) < 1:
            print("用法：python isa_tool.py unpack <data.isa> [out_dir]")
            return 1
        path = rest[0]
        out_dir = rest[1] if len(rest) > 1 else os.path.splitext(path)[0] + "_unpacked"
        return cmd_unpack(path, out_dir)

    elif cmd == "pack":
        if len(rest) < 2:
            print("用法：python isa_tool.py pack <dir> <out.isa>")
            return 1
        return cmd_pack(rest[0], rest[1])

    elif cmd == "verify":
        if len(rest) < 1:
            print("用法：python isa_tool.py verify <data.isa>")
            return 1
        return cmd_verify(rest[0])

    else:
        # 拖放场景：把 .isa 当输入直接 unpack
        if cmd.lower().endswith(".isa"):
            path = cmd
            out_dir = os.path.splitext(path)[0] + "_unpacked"
            return cmd_unpack(path, out_dir)
        print("未知子命令：%s" % cmd)
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
