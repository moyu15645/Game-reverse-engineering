# -*- coding: utf-8 -*-
"""
ism_extract.py — ISM 脚本纯文本提取器（《タイムカプセル “春”》）

从 .ISM 脚本提取可翻译文本（人名 / 台词 / 语音），输出 JSON。

用法：
    python ism_extract.py <input.ism> [-o out.json]        # 提取单个脚本
    python ism_extract.py <input_dir>  [-o out_dir]         # 批量提取目录下所有 .ISM
    python ism_extract.py <input.ism> --format txt          # 输出纯文本（逐行）

支持拖放：把 .ISM 文件/文件夹拖到本脚本图标上即可。
依赖 opcodelist.py、disassembler.py（同目录）。

────────────────────────────────────────────────────────────────────
提取原理（已逆向实证）：
    显示消息由 MSG(0x2C) 指令触发，其前按固定顺序压入 3 个字符串引用：
        [人名]   STRREF -> 人名字符串  或  STR "人名"（内联）
        [台词]   STR "台词正文"
        [语音]   STR "xxx.ogg"         或  STRREF -> 空串（无语音）

    人名池机制：第一次出现人名用内联 STR，后续用 STRREF 引用同一位置。
    旁白：人名与语音均用 STRREF -> 空串占位。
"""

import os
import sys
import json
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disassembler import parse_header, decrypt_33, sweep_code  # noqa: E402
from opcodelist import OPCODES  # noqa: E402

DEFAULT_ENCODING = "cp932"

OP_STR = 0x33
OP_STRREF = 0x45
OP_MSG = 0x2C


def extract(data, encoding=DEFAULT_ENCODING):
    """从 .ISM 二进制提取文本，返回 (records, header)。
    records[i] = dict(offset, name, message, voice)
    """
    header = parse_header(data)
    code_base = header["code_base"]
    instructions, tables, unknown = sweep_code(data, code_base)

    # 先解密所有 STR，建立 file_off -> 内容 映射
    str_map = {}  # file_off -> bytes 内容
    for foff, op in instructions:
        if op == OP_STR:
            plain, _ = decrypt_33(data, foff, code_base)
            str_map[foff] = plain

    def resolve(ref_target):
        """STRREF 目标 -> 字符串内容 bytes（无则 None）。"""
        return str_map.get(ref_target)

    def _is_voice(c):
        return c.lower().endswith(b".ogg")

    def _is_resource(c):
        l = c.lower()
        return any(l.endswith(x) for x in (b".png", b".ism", b".iss", b".mpg",
                                            b".isg", b".isf", b".bmp"))

    def _is_debug(c):
        s = c.decode(encoding, errors="replace")
        if "%d" in s:
            return True
        if s.startswith("script ") or s.startswith(" line"):
            return True
        if s.startswith("Script has been"):
            return True
        if "auto save" in s:
            return True
        if s.startswith("Nov ") or s.startswith("Jan ") or s.startswith("Feb "):
            return True
        # 编译时间戳 HH:MM:SS
        if len(s) == 8 and s[2] == ":" and s[5] == ":" and s.replace(":", "").isdigit():
            return True
        return False

    records = []
    n = len(instructions)

    for idx in range(n):
        foff, op = instructions[idx]
        if op != OP_MSG:
            continue

        # 以 MSG 为锚，向前回溯到段边界（上一个 MSG / RET / END），
        # 收集这一段内的所有字符串引用（含资源、语音、台词、人名）。
        refs = []  # 从近到远
        j = idx - 1
        while j >= 0:
            jf, jop = instructions[j]
            if jop in (OP_MSG, 0x25, 0x29):  # MSG / RET / END
                break
            if jop == OP_STR:
                refs.append(("str", jf, str_map.get(jf, b"")))
            elif jop == OP_STRREF:
                r = struct.unpack("<I", data[jf + 1:jf + 5])[0]
                refs.append(("ref", jf, resolve(code_base + r)))
            j -= 1
        refs.reverse()  # 从远到近

        # 过滤资源引用（背景图 / 立绘 / 脚本名等）
        meaningful = [(k, f, c) for (k, f, c) in refs if c and not _is_resource(c)]

        # 找语音锚点：最后一个 .ogg
        voice = ""
        voice_idx = -1
        for i in range(len(meaningful)):
            if _is_voice(meaningful[i][2]):
                voice_idx = i
                voice = meaningful[i][2].decode(encoding, errors="replace")

        if voice_idx >= 0:
            # 台词 = 语音前面紧邻的非调试文本
            msg_idx = voice_idx - 1
            while msg_idx >= 0 and (not meaningful[msg_idx][2] or _is_debug(meaningful[msg_idx][2])):
                msg_idx -= 1
            name_idx = msg_idx - 1
        else:
            # 无语音：台词 = 最后一个非调试文本，人名在其前
            msg_idx = len(meaningful) - 1
            while msg_idx >= 0 and (not meaningful[msg_idx][2] or _is_debug(meaningful[msg_idx][2]) or _is_voice(meaningful[msg_idx][2])):
                msg_idx -= 1
            name_idx = msg_idx - 1

        if msg_idx < 0:
            continue  # 无文本消息（清屏等），跳过

        msg_ref = meaningful[msg_idx]
        msg_s = (msg_ref[2] or b"").decode(encoding, errors="replace")

        name_s = ""
        name_off = None
        if name_idx >= 0:
            n_kind, n_fo, nc = meaningful[name_idx]
            if nc and not _is_debug(nc):
                name_s = nc.decode(encoding, errors="replace")
            # name 池的实际 STR 偏移：内联 STR = 自身；STRREF = 目标
            if n_kind == "str":
                name_off = n_fo
            else:
                rr = struct.unpack("<I", data[n_fo + 1:n_fo + 5])[0]
                name_off = code_base + rr

        records.append({
            "offset": "0x%X" % msg_ref[1],
            "name": name_s,
            "name_offset": "0x%X" % name_off if name_off is not None else "",
            "message": msg_s,
            "voice": voice,
        })

    # ---- Orphan narration 扫描 ----
    # 兜底：有些 narration 在 MSG 之间或最后 MSG 之后才 push（如 TC_OP.ISM 0x17EF
    # 的 "それから７年―――"，它在 MSG 0x17DD 之后、0x1863 之前；两个 MSG 的前向
    # 后向扫描都抓不到）。这里扫描每个 segment 内所有 MSG 间隙。
    used_offsets = {int(r["offset"], 16) for r in records if r.get("offset")}
    seg_ends = [i for i, (fo, op) in enumerate(instructions) if op in (0x25, 0x29)]
    segments = []
    st = 0
    for b in seg_ends + [n]:
        segments.append((st, b))
        st = b + 1
    for seg_s, seg_e in segments:
        msgs = [i for i in range(seg_s, seg_e) if instructions[i][1] == OP_MSG]
        if not msgs:
            continue
        gaps = []
        if msgs[0] > seg_s:
            gaps.append((seg_s, msgs[0]))
        for mi in range(len(msgs)):
            gs = msgs[mi] + 1
            ge = msgs[mi + 1] if mi + 1 < len(msgs) else seg_e
            if gs < ge:
                gaps.append((gs, ge))
        for gap_s, gap_e in gaps:
            pending_name = b""
            has_pending = False
            last_ref_fo = None
            for k in range(gap_s, gap_e):
                kf, kop = instructions[k]
                if kop == OP_STRREF:
                    rr = struct.unpack("<I", data[kf + 1:kf + 5])[0]
                    pending_name = resolve(code_base + rr) or b""
                    has_pending = True
                elif kop == OP_STR and has_pending:
                    if kf not in used_offsets:
                        plain, _ = decrypt_33(data, kf, code_base)
                        if plain and not _is_resource(plain) and not _is_voice(plain):
                            if not _is_debug(plain):
                                s_dec = plain.decode(encoding, errors="replace")
                                voice_s = ""
                                voice_off = None
                                for kk in range(k + 1, gap_e):
                                    if instructions[kk][1] == OP_STRREF:
                                        rr2 = struct.unpack("<I", data[instructions[kk][0] + 1:instructions[kk][0] + 5])[0]
                                        v = resolve(code_base + rr2)
                                        if v and _is_voice(v):
                                            voice_s = v.decode(encoding, errors="replace")
                                        break
                                # name_offset: 内联 STR 用自身；STRREF 已在上一轮设 pending_name
                                records.append({
                                    "offset": "0x%X" % kf,
                                    "name": pending_name.decode(encoding, errors="replace") if pending_name else "",
                                    "name_offset": "0x%X" % (code_base + struct.unpack("<I", data[last_ref_fo + 1:last_ref_fo + 5])[0]) if last_ref_fo else "",
                                    "message": s_dec,
                                    "voice": voice_s,
                                })
                                used_offsets.add(kf)
                                has_pending = False
                if kop == OP_STRREF:
                    last_ref_fo = kf

    return records, header


def to_json(records, filename, encoding=DEFAULT_ENCODING):
    """转成备忘录约定的 JSON 结构（id / name / name_offset / pre_jp / message / voice / offset）。"""
    out = []
    for i, r in enumerate(records, 1):
        out.append({
            "id": i,
            "name": r["name"],
            "name_offset": r.get("name_offset", ""),
            "pre_jp": r["message"],
            "message": r["message"],
            "voice": r["voice"],
            "offset": r["offset"],
        })
    return out


def to_txt(records, filename):
    """转成可读纯文本（每行一条，格式：名字：台词）。"""
    lines = []
    for i, r in enumerate(records, 1):
        if r["name"]:
            lines.append("%d\t[%s]\t%s" % (i, r["name"], r["message"]))
        else:
            lines.append("%d\t%s" % (i, r["message"]))
    return "\n".join(lines)


def process_file(path, out_path, fmt, encoding):
    with open(path, "rb") as f:
        data = f.read()
    records, header = extract(data, encoding)
    filename = os.path.basename(path)

    if fmt == "json":
        content = json.dumps(to_json(records, filename), ensure_ascii=False, indent=2)
    else:
        content = to_txt(records, filename)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content + "\n")
    else:
        print(content)
    return records


def main(argv):
    args = list(argv)
    out = None
    fmt = "json"
    encoding = DEFAULT_ENCODING
    inputs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--output"):
            out = args[i + 1]
            i += 2
        elif a == "--format":
            fmt = args[i + 1]
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

    total_records = 0
    for inp in inputs:
        if os.path.isdir(inp):
            out_dir = out or os.path.join(inp, "_text")
            os.makedirs(out_dir, exist_ok=True)
            for fn in sorted(os.listdir(inp)):
                if fn.lower().endswith(".ism"):
                    full = os.path.join(inp, fn)
                    op = os.path.join(out_dir, os.path.splitext(fn)[0] + ("." + fmt))
                    recs = process_file(full, op, fmt, encoding)
                    total_records += len(recs)
                    print("提取：%s -> %s（%d 条）" % (fn, op, len(recs)))
            print("共提取 %d 条文本" % total_records)
        else:
            op = out or os.path.splitext(inp)[0] + ("." + fmt)
            recs = process_file(inp, op, fmt, encoding)
            total_records = len(recs)
            print("提取：%s -> %s（%d 条）" % (inp, op, len(recs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
