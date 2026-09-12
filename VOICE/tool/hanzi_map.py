"""hanzi2kanji_table.txt 映射处理器

表格式：每行 "简体字<TAB>替换字"。按用户约定——
  · 键 = 翻译文本里出现的字；值 = 写入游戏字节流时应使用的字
  · 表里没涉及的字保持原样
  · 不修改映射表本身
  · 映射只应用一次（禁止二次归一化）

注意：该表是「字体槽/日繁替换」混用表（例：`你 -> 凜`），只在游戏字体做过
对应替换时才成立。LOVELESS 使用的是系统 MS Gothic，若显示异常请以
--no-map 重新生成。

用法（作为模块）:
    import hanzi_map
    m = hanzi_map.load()
    out = hanzi_map.convert(text, m)
"""
import os

DEFAULT_TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'hanzi2kanji_table.txt')

# 常见会让 cp932 编码失败或渲染异常的符号，按用户要求直接删除
DROP_CHARS = '♪♫♬♩♭♯♮♪゛'


def load(path=None):
    path = path or DEFAULT_TABLE
    table = {}
    if not os.path.exists(path):
        return table
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or '\t' not in line:
                continue
            k, v = line.split('\t', 1)
            if len(k) == 1:
                table[k] = v
    return table


def convert(text, table):
    """Apply the mapping exactly once (verbatim, as instructed)."""
    return ''.join(table.get(ch, ch) for ch in text)


def _encodable(ch):
    try:
        ch.encode('cp932')
        return True
    except UnicodeEncodeError:
        return False


def convert_safe(text, table):
    """Apply the mapping only where it is actually needed for encodability.

    The shipped table is a *mixed* table: most entries are genuine
    simplified -> Japanese/traditional forms (说->説, 时->時), but some are
    FONT SLOT assignments from another game's patched font (喂->鶫, 啦->鼕,
    你->凜). Those slots do not exist in LOVELESS, which draws with the system
    MS Gothic font, so applying them would display a completely unrelated
    glyph. A character that already encodes fine in cp932 is already present in
    the Japanese font, hence it is used unchanged.
    """
    out = []
    for ch in text:
        if _encodable(ch):
            out.append(ch)
        else:
            out.append(table.get(ch, ch))
    return ''.join(out)


def to_cp932(text, drop=DROP_CHARS):
    """Encode to cp932, dropping characters that cannot be represented.

    Returns (bytes, dropped_list).
    """
    out = bytearray()
    dropped = []
    for ch in text:
        if ch in drop:
            continue
        try:
            out += ch.encode('cp932')
        except UnicodeEncodeError:
            dropped.append(ch)
    return bytes(out), dropped


def main():
    import sys
    t = load()
    print('table entries: %d' % len(t))
    if len(sys.argv) > 1:
        s = sys.argv[1]
        c = convert(s, t)
        b, d = to_cp932(c)
        print('in :', s)
        print('map:', c)
        print('hex:', b.hex())
        print('drop:', d)


if __name__ == '__main__':
    main()
