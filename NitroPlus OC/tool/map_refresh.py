#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
map_refresh.py —— 按映射表批量刷新翻译 JSON 的 message（简体 → 日繁字形 / JIS 码位）
================================================================================
用途：把 LLM 译好的简体中文，按映射表逐字换成游戏目标编码（CP932/JIS）能表示的字形，
      并检查是否还有「刷不掉、也编不进 CP932」的漏网字符。

适用对象：Angel Nyanya（喵喵）项目 output/ 下的 xxx.json（列表形式，
          字段 id / name? / pre_jp / message），也兼容任何同构 JSON。

映射表支持两种格式（自动识别，可叠加多个 --map）
  1) JSON 字典：{"这":"這", "说":"説", ...}        ← scripts/subs_cn_jp.json
  2) 两列 TSV  ：简体<Tab>字形                       ← hanzi2kanji_table.txt
     · 以 # 开头、无 Tab、以及含“基本格式/特殊格式”的说明行会被跳过
     · 4 列条件规则（Angel Nyanya 的 [!]/[and]/[or] 语法）不做猜测性执行，
       会被列出并跳过（这类规则请在 Angel Nyanya 自己的“译后校正”里跑）

替换规则
  · 长键优先；一次扫描完成（替换结果不再参与匹配）→ 幂等，重复运行结果不变
  · 只改 message（--with-name 时同时改 name）；pre_jp 永远不动
  · 不做其它任何加工（不加空格、不动 <K>、不动全角空格与标点）

用法
----
  python map_refresh.py                        # 默认：./output → ./output_mapped
  python map_refresh.py <file|目录> [-o 输出目录]
  python map_refresh.py <目录> --check         # 只体检不写文件
  python map_refresh.py <目录> --inplace       # 就地覆盖（自动 .bak 备份）
  python map_refresh.py <目录> -o ../text/script   # 直接刷进注入用的目录
  --map 表1.json --map 表2.txt                # 多表按顺序叠加
  --encoding cp932                            # 目标编码（默认 cp932）
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter

MAP_NAMES = [
    'scripts/subs_cn_jp.json',      # Angel Nyanya 的 CN→JP 映射表
    'subs_cn_jp.json',
    'hanzi2kanji_table.txt',        # 两列 TSV 形式的同类映射表
    'Dict/hanzi2kanji_table.txt',
    '补充映射.txt',                  # 漏网字符的补充表（体检报告产生）
]
HEADER_HINTS = ('基本格式', '特殊格式', '替换前原文', 'JP_Name', 'CN_Name')


def find_default_maps(target):
    """在 当前目录 / 目标目录 / 其父目录 中按序查找默认映射表（已存在者优先）"""
    tdir = target if os.path.isdir(target) else os.path.dirname(os.path.abspath(target))
    dirs, seen = [], set()
    for d in ('.', tdir, os.path.dirname(os.path.abspath(tdir)), os.path.abspath(os.path.join(tdir, '..'))):
        a = os.path.abspath(d)
        if a not in seen and os.path.isdir(a):
            seen.add(a)
            dirs.append(a)
    found = []
    for d in dirs:
        for n in MAP_NAMES:
            p = os.path.join(d, n)
            if os.path.exists(p) and p not in found:
                found.append(p)
    return found


# ------------------------------------------------------------------ 载入映射表
def unescape_cell(s: str) -> str:
    r"""TSV 单元格里的 \uXXXX / \t / \n / \\ 转义（便于书写不可见字符）"""
    out, i = [], 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == 'u' and i + 6 <= len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nxt == 't':
                out.append('\t'); i += 2; continue
            if nxt == 'n':
                out.append('\n'); i += 2; continue
            if nxt == '\\':
                out.append('\\'); i += 2; continue
        out.append(s[i]); i += 1
    return ''.join(out)


def load_map(path):
    """返回 (mapping: dict[str,str], notes: list[str])"""
    if not os.path.exists(path):
        raise FileNotFoundError(f'映射表不存在: {path}')
    notes = []
    ext = os.path.splitext(path)[1].lower()
    if ext == '.json':
        d = json.load(open(path, encoding='utf-8'))
        if not isinstance(d, dict):
            raise ValueError(f'{path}: JSON 映射表应为 {{"简":"繁"}} 字典')
        mp = {str(k): str(v) for k, v in d.items() if str(k)}
        notes.append(f'JSON 映射 {len(mp)} 条')
        return mp, notes
    mp, fourcol, bad = {}, [], 0
    for ln, line in enumerate(open(path, encoding='utf-8-sig'), 1):
        line = line.rstrip('\r\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if any(h in line for h in HEADER_HINTS) and '\t' not in line:
            continue
        parts = line.split('\t')
        if len(parts) == 1:
            if line.strip() and not line.startswith('#'):
                bad += 1
            continue
        if len(parts) >= 4:                     # 条件规则：不猜语义，只登记
            fourcol.append((ln, line))
            continue
        k, v = unescape_cell(parts[0]), unescape_cell(parts[1]) if len(parts) > 1 else ''
        if k == '':
            continue
        mp[k] = v
    notes.append(f'TSV 映射 {len(mp)} 条')
    if fourcol:
        notes.append(f'跳过 4 列条件规则 {len(fourcol)} 条（需在 Angel Nyanya 的译后校正中执行）')
    if bad:
        notes.append(f'跳过无 Tab 的说明/异常行 {bad} 条')
    return mp, notes


class Mapper:
    """长键优先、单遍替换（幂等）"""

    def __init__(self, table: dict):
        self.table = {k: v for k, v in table.items() if k != v}
        self.by_first = {}
        for k in sorted(self.table, key=len, reverse=True):
            self.by_first.setdefault(k[0], []).append(k)
        self.hits = Counter()

    def __call__(self, s: str) -> str:
        if not s:
            return s
        out, i, n = [], 0, len(s)
        while i < n:
            cands = self.by_first.get(s[i])
            hit = None
            if cands:
                for k in cands:
                    if s.startswith(k, i):
                        hit = k
                        break
            if hit:
                out.append(self.table[hit])
                self.hits[hit] += 1
                i += len(hit)
            else:
                out.append(s[i])
                i += 1
        return ''.join(out)


# ------------------------------------------------------------------ 标签修复 / 记号体检
TAG_RE = re.compile(r'<[^<>]*>')
MRK_RE = re.compile(r'<[^<>]*>')
# LLM 常把标签里的半角属性引号写成全角引号/书名号 → 引擎无法解析该标签
CURLY = '“”‘’『』《》＂「」〝〟'
ATTR_Q = re.compile(r'([A-Za-z_][A-Za-z0-9_]*\s*=\s*)[' + CURLY + r']([^<>"]*?)[' + CURLY + r']')


def fix_tags(s: str):
    """只修 <…> 内部、且只修“属性定界符”位置的全角引号（标签外与引号内的正文不动）"""
    if '<' not in s:
        return s, 0
    n = 0

    def _in_tag(m):
        nonlocal n
        tag = m.group(0)

        def _a(am):
            nonlocal n
            n += 1
            return am.group(1) + '"' + am.group(2) + '"'

        return ATTR_Q.sub(_a, tag)

    return TAG_RE.sub(_in_tag, s), n


def marker_sig(s):
    """正文里内嵌记号的“种类序列”（用于判断翻译是否弄丢/弄坏记号）"""
    return [re.match(r'</?([A-Za-z0-9_]*)', m).group(1).upper() for m in MRK_RE.findall(s)]


def _tag_name(t):
    m = re.match(r'</?\s*([A-Za-z0-9_]*)', t)
    return m.group(1).upper() if m else t


def restore_tags(msg, src):
    """★ 用原文的标签整体覆盖译文里的标签。

    标签内部是【资源名 / 数值 / 属性定界符】，一律不允许被翻译或字形映射：
      · `<se SRC="薫カツサンド飲み込む5">`  资源名被译 → 音效直接找不到文件
      · `<BUSTUP NAME="奈都美KW" BODY="私服驚き弱">`  立绘名被译 → 立绘不显示
      · `<wait time="5">`  引号被写成全角 → 引擎忽略该标签
    仅在「标签数量相同、且标签名序列一致」时整体还原；否则原样返回（记入体检表）。

    需要翻译的属性值（`<VOICE NAME=…>` 角色名、`<CHOICE TEXT=…>` 选项、
    `<R TEXT=…>` 注音）都已作为独立条目单独提取，故整体还原不会漏译任何内容。

    返回 (新串, 被还原的标签数)
    """
    p = TAG_RE.findall(src or '')
    m = TAG_RE.findall(msg or '')
    if not p or len(p) != len(m):
        return msg, 0
    if [_tag_name(x) for x in p] != [_tag_name(x) for x in m]:
        return msg, 0
    cnt = [0]

    def _r(mo):
        i = cnt[0]
        cnt[0] += 1
        return p[i]

    return TAG_RE.sub(_r, msg), sum(1 for a, b in zip(p, m) if a != b)


# ------------------------------------------------------------------ 体检
def check_encoding(messages, enc):
    """返回 (无法编码字符计数, [(id, 字符, 上下文)])"""
    bad = Counter()
    samples = []
    for mid, msg in messages:
        try:
            msg.encode(enc)
        except UnicodeEncodeError as e:
            ch = msg[e.start:e.start + 1]
            bad[ch] += 1
            if len(samples) < 40:
                ctx = msg[max(0, e.start - 12):e.start + 12]
                samples.append((mid, ch, ctx))
    return bad, samples


def process_file(path, mapper, enc, with_name, name_map, dry, fix_tag=True, srcmap=None):
    data = json.load(open(path, encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError(f'{path}: 顶层不是列表')
    changed = rep = 0
    msgs = []
    name_hits = []
    mstats = Counter()
    msamples = []
    for e in data:
        if 'message' in e and isinstance(e['message'], str):
            orig = e['message']
            new = mapper(orig)
            # ★ 标签内是资源名/数值，用原文整体覆盖（同时修掉模型写出的全角引号）
            new, ntag = restore_tags(new, e.get('pre_jp') or '')
            mstats['标签还原'] += ntag
            if fix_tag:
                new, nq = fix_tags(new)
                mstats['标签引号修复'] += nq
            if new != orig:
                changed += 1
                rep += sum(1 for a, b in zip(orig, new) if a != b) or 1
                if not dry:
                    e['message'] = new
            msgs.append((e.get('id'), new))
            # 与原文比对内嵌记号是否被翻译弄丢/弄坏（原文即本条 pre_jp）
            s0 = e.get('pre_jp') or (srcmap or {}).get(e.get('id'))
            if s0:
                i0, i1 = marker_sig(s0), marker_sig(new)
                if i0 != i1:
                    lost = [x for x in i0 if x not in i1]
                    mstats['记号丢失'] += 1
                    if len(msamples) < 400:
                        msamples.append((e.get('id'), '丢失 ' + (''.join(lost) or '顺序变化'),
                                         s0[:60], new[:60]))
                elif MRK_RE.findall(s0) != MRK_RE.findall(new):
                    mstats['记号内容变化'] += 1
                    if len(msamples) < 400:
                        msamples.append((e.get('id'), '内容变化',
                                         ''.join(MRK_RE.findall(s0))[:60],
                                         ''.join(MRK_RE.findall(new))[:60]))
        if with_name and isinstance(e.get('name'), str) and e['name']:
            nm = e['name']
            if name_map:
                nm2 = name_map(nm)
                if nm2 != nm:
                    name_hits.append((e.get('id'), e['name'], nm2))
                    if not dry:
                        e['name'] = nm2
    return data, dict(entries=len(data), changed=changed, msgs=msgs, name_hits=name_hits,
                      mstats=mstats, msamples=msamples)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('target', nargs='?', default=None, help='单个 json 或目录（默认 ./output）')
    ap.add_argument('-o', '--out', help='输出目录（默认 <target>_mapped；文件输入时默认原地 _mapped 文件）')
    ap.add_argument('--map', action='append', default=[], help='映射表，可多次指定')
    ap.add_argument('--encoding', default='cp932', help='目标编码，默认 cp932')
    ap.add_argument('--with-name', action='store_true', help='同时刷新 name 字段')
    ap.add_argument('--name-map', help='人名映射表（JSON: {"日文名":"中文名"}）')
    ap.add_argument('--check', action='store_true', help='只体检，不写文件')
    ap.add_argument('--inplace', action='store_true', help='就地覆盖（自动 .bak 备份）')
    ap.add_argument('--no-fix-tags', dest='fix_tags', action='store_false',
                    help='不修复标签内被译成全角引号的属性定界符（默认修复）')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    if len(sys.argv) == 1:
        pass
    elif '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        return
    a = ap.parse_args()

    # 目标与输出
    tgt = a.target or 'output'
    if not os.path.exists(tgt):
        print(f'[错误] 找不到目标: {tgt}\n请把 JSON 或目录传给本程序，或在 Angel Nyanya 目录下运行（默认 ./output）')
        sys.exit(1)
    files = ([os.path.join(tgt, f) for f in sorted(os.listdir(tgt))
              if f.lower().endswith('.json') and not f.lower().endswith('.meta.json')]
             if os.path.isdir(tgt) else [tgt])
    if not files:
        print(f'[错误] {tgt} 下没有 json')
        sys.exit(1)
    if a.inplace:
        outdir = None
        outfile = None
    elif a.out:
        outdir, outfile = (a.out, None) if os.path.isdir(tgt) or len(files) > 1 else (None, a.out)
    else:
        if os.path.isdir(tgt):
            outdir, outfile = tgt.rstrip('\\/') + '_mapped', None
        else:
            b, e = os.path.splitext(tgt)
            outdir, outfile = None, b + '_mapped' + e

    # 映射表
    maps = a.map or find_default_maps(tgt)
    if not maps:
        print('[错误] 未找到映射表。请用 --map 指定（如 --map scripts/subs_cn_jp.json）')
        sys.exit(1)
    table = {}
    print('=' * 78)
    print('映射表:')
    for m in maps:
        mp, notes = load_map(m)
        table.update(mp)                 # 后表覆盖前表
        print(f'  + {m}  ({"; ".join(notes)})')
    print(f'  => 合并后可用映射 {len(table)} 条')
    mapper = Mapper(table)
    nmapper = None
    if a.name_map:
        nd = json.load(open(a.name_map, encoding='utf-8'))
        nmapper = Mapper({str(k): str(v) for k, v in nd.items()})
        print(f'  + 人名映射 {len(nd)} 条: {a.name_map}')

    print('=' * 78)
    print(f'目标编码: {a.encoding}   文件数: {len(files)}   '
          f'{"【体检模式：不写文件】" if a.check else ("【就地覆盖】" if a.inplace else "输出目录: " + str(outdir or outfile))}')
    tot_ent = tot_chg = 0
    all_bad = Counter()
    all_samples = []
    all_mstat = Counter()
    all_msamples = []
    for f in files:
        try:
            data, st = process_file(f, mapper, a.encoding, a.with_name, nmapper, a.check,
                                    fix_tag=a.fix_tags)
        except Exception as ex:
            print(f'[跳过] {os.path.basename(f)}: {ex}')
            continue
        tot_ent += st['entries']
        tot_chg += st['changed']
        all_mstat.update(st['mstats'])
        all_msamples += [(os.path.basename(f),) + s for s in st['msamples']]
        bad, samples = check_encoding(st['msgs'], a.encoding)
        all_bad.update(bad)
        all_samples += [(os.path.basename(f),) + s for s in samples[:6]]
        if not a.check:
            # 保持字段顺序 id → name → pre_jp → message
            out = []
            for e in data:
                d = {}
                for k in ('id', 'name', 'pre_jp', 'message'):
                    if k in e:
                        d[k] = e[k]
                for k, v in e.items():
                    if k not in d:
                        d[k] = v
                out.append(d)
            if a.inplace:
                p = f
                if not os.path.exists(p + '.bak'):
                    shutil.copy2(p, p + '.bak')
            elif outdir:
                os.makedirs(outdir, exist_ok=True)
                p = os.path.join(outdir, os.path.basename(f))
            else:
                p = outfile
            json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        if not a.quiet and (st['changed'] or bad):
            flag = 'OK ' if not bad else '!! '
            print(f'{flag}{os.path.basename(f):28s} 条目 {st["entries"]:4d} 改写 {st["changed"]:4d} '
                  f'剩余不可编码 {sum(bad.values()):3d}')
    # 汇总
    print('=' * 78)
    print(f'合计: {len(files)} 个文件 / {tot_ent} 条；message 被改写 {tot_chg} 条')
    top = mapper.hits.most_common(25)
    if top:
        print('替换频次 Top25: ' + '  '.join(f'{k}→{table[k]}×{v}' for k, v in top))
    print(f'使用到的映射条目: {len(mapper.hits)} 种')
    if all_mstat:
        print('\n内嵌记号体检（正文里的 <K> / <R> / <se> 等是否被翻译弄坏）:')
        for k, v in all_mstat.most_common():
            print(f'   {k}: {v}')
        rpt_dir = (outdir if (outdir and not a.check)
                   else (tgt if os.path.isdir(tgt) else os.path.dirname(os.path.abspath(tgt))))
        rpt = os.path.join(rpt_dir, '_记号体检.tsv')
        try:
            with open(rpt, 'w', encoding='utf-8') as fo:
                fo.write('文件\tid\t问题\t原文记号/原文\t译文记号/译文\n')
                for f, mid, kind, a1, b1 in all_msamples:
                    fo.write(f'{f}\t{mid}\t{kind}\t{a1}\t{b1}\n')
            print(f'   明细已写入: {rpt}')
        except Exception as ex:
            print(f'   （明细写入失败: {ex}）')
    if all_bad:
        print(f'\n!! 仍有 {sum(all_bad.values())} 处字符无法用 {a.encoding} 编码（{len(all_bad)} 种），'
              f'回写游戏会乱码/报错：')
        for ch, c in all_bad.most_common(60):
            try:
                name = f'U+{ord(ch):04X}'
            except Exception:
                name = '?'
            print(f'   {ch!r:8s} {name}  ×{c}')
        print('   样例上下文：')
        for f, mid, ch, ctx in all_samples[:15]:
            print(f'     {f}  id={mid}  {ch!r}  ...{ctx}...')
        print('   → 处理办法：把缺的字符补进映射表（如 hanzi2kanji_table.txt 或 subs_cn_jp.json），')
        print('     或把这些词的译法改写成 CP932 可表示的字形后重刷。')
        print('   （用 --check 可反复体检，直到清零再回写游戏）')
    else:
        print(f'\n[OK] 全部 message 均可被 {a.encoding} 编码，可以回写游戏。')
    if not a.check and not a.quiet:
        print(f'\n完成：{outdir or outfile or "（就地覆盖，已备份 .bak）"}')
        print('提示：注入回脚本用  python tool/nps_text.py inject <脚本目录> <本目录> -o <输出目录>')


if __name__ == '__main__':
    main()
