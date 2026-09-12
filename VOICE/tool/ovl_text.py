"""LOVELESS .OVL 脚本：文本区扫描 / 提取 / 就地注入。

引擎（Game.exe, HAM/LOVELESS 引擎）的脚本 VM 把「显示文本」直接内嵌在指令流里：
VM 主循环（FUN_00410530）对每个字节查两张分类表，凡是"非操作码"的字节就
累加进消息缓冲 local_230；遇到操作码则先把缓冲刷新到消息窗口
（FUN_00420420，左对齐绘制、超宽自动折行）。

因此一条文本 = 指令流中一段"非操作码字节"的连续区间，观测到的典型形态：

    0b 05 <var98> ff ff ff ff     ; 计数
    0b 04 <var51> <var98>         ; 赋值
    0b 38 <u16 off>               ; 调用公共库(文件头 ~1800 字节)的文本例程
    <TEXT ...>                    ; ← 文本区（CP932）
    01                            ; 消息结束 / 等待按键
    ...
    0x0A                          ; 文本内部的换行操作码（把一句拆成多个物理行）

因为脚本里存在大量「绝对 16 位偏移」指令（例如 0b 32..0b 39 = 条件/无条件跳转，
操作数就是脚本内绝对偏移），**变长替换会打断控制流**。所以本工具采用
「定长就地替换」：译文按原文本区的字节长度精确回填，不足处用 0x20 补齐。
这样脚本除文本字节外一个字节都不动，VM 行为不可能改变。

文本区边界判定不依赖完整反汇编，而是复现 VM 的分类规则：
  文本字节 = 合法 CP932 双字节字符 | ASCII 0x20..0x7E（除 '#','$','%' 三个操作码）| 0xA1..0xDF 半角片假名
  其余（0x00-0x1F, 0x7F, 落单的 0x80..0xFF）= 操作码 / 分隔
"""
import re

# 顶层操作码（Game.exe FUN_00410530 的 switch case）
TOP_OPCODES = {0x01, 0x03, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0A, 0x0B,
               0x0D, 0x0E, 0x0F, 0x11, 0x13, 0x15, 0x16, 0x19, 0x23, 0x24, 0x25}
# ASCII 里属于操作码的（# $ %），不能算文本
ASCII_OPCODES = {0x23, 0x24, 0x25}

LEAD = [(0x81, 0x9F), (0xE0, 0xFC)]
TRAIL = [(0x40, 0x7E), (0x80, 0xFC)]


def _is_lead(b):
    return any(lo <= b <= hi for lo, hi in LEAD)


def _is_trail(b):
    return any(lo <= b <= hi for lo, hi in TRAIL)


def scan_runs(data):
    """Return list of dicts: {start, length, raw}"""
    runs = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if _is_lead(b) and i + 1 < n and _is_trail(data[i + 1]):
            i += 2
            continue
        if 0x20 <= b <= 0x7E and b not in ASCII_OPCODES:
            i += 1
            continue
        if 0xA1 <= b <= 0xDF:
            i += 1
            continue
        # opcode / separator
        i += 1
    # second pass: collect the "text" positions
    is_text = bytearray(n)
    i = 0
    while i < n:
        b = data[i]
        if _is_lead(b) and i + 1 < n and _is_trail(data[i + 1]):
            is_text[i] = 1
            is_text[i + 1] = 1
            i += 2
            continue
        if (0x20 <= b <= 0x7E and b not in ASCII_OPCODES) or 0xA1 <= b <= 0xDF:
            is_text[i] = 1
        i += 1
    i = 0
    while i < n:
        if is_text[i]:
            j = i
            while j < n and is_text[j]:
                j += 1
            runs.append({'start': i, 'length': j - i, 'raw': data[i:j]})
            i = j
        else:
            i += 1
    return runs


def dbl_byte_count(raw):
    c = 0
    i = 0
    while i < len(raw):
        if _is_lead(raw[i]) and i + 1 < len(raw) and _is_trail(raw[i + 1]):
            c += 1
            i += 2
        else:
            i += 1
    return c


def meaningful_runs(data, min_len=4, min_dbl=2):
    out = []
    for r in scan_runs(data):
        if r['length'] < min_len:
            continue
        if dbl_byte_count(r['raw']) < min_dbl:
            continue
        r['pre'] = data[max(0, r['start'] - 6):r['start']]
        r['post'] = data[r['start'] + r['length']:r['start'] + r['length'] + 4]
        try:
            r['text'] = r['raw'].decode('cp932')
        except UnicodeDecodeError:
            continue
        out.append(r)
    return out


def replace_run(data, start, length, encoded):
    """In-place fixed-length replacement (pad with 0x20)."""
    if len(encoded) > length:
        return None
    blob = encoded + b'\x20' * (length - len(encoded))
    return data[:start] + blob + data[start + length:]


if __name__ == '__main__':
    import os
    import sys
    UNP = r'E:\potential\LOVELESS終わらない物語\LOVELESS\unpack'
    fn = sys.argv[1] if len(sys.argv) > 1 else 'LLS101.OVL'
    data = open(os.path.join(UNP, fn), 'rb').read()
    rs = meaningful_runs(data)
    print('%s size=%d  text runs=%d' % (fn, len(data), len(rs)))
    for r in rs[:40]:
        print('  @%#06x len=%3d dbl=%2d pre=%-12s post=%-8s %r' %
              (r['start'], r['length'], dbl_byte_count(r['raw']),
               r['pre'].hex(), r['post'].hex(), r['text']))
