#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nps_disasm.py —— 「Hello,world.」(NitroPlus / OC 引擎) .nps 脚本反汇编器
================================================================================
把 CP932 文本标记脚本 .nps 解析为**结构化语义汇编** .nps.asm.txt。

.asm.txt 结构
-------------
  ; ...                                  注释行（汇编器忽略）
  .file <包内路径>                       元信息（. 开头）
  .encoding cp932 / .eol crlf / .final_nl yes|no
  .scene / .includes / .markers / .jumps / .choices / .counts / .tags
  ; 语义注释（; 开头）
  Lnnnnnn  <KIND>  <原始行内容>          记录行：类型 + 逐字节原样负载

  KIND:  BLK=空行  CMT=注释  TAG=纯命令  HTM=HTML 残留  TXT=纯文本  MIX=文本+行内记号
         Q=负载首尾含空白，用引号+转义保存（汇编器还原）
  （负载永远是原始行内容本身，不含行尾换行；行尾由 .eol / .final_nl 决定，
    因此 汇编器 = 剥掉类型前缀 → 逐字节还原，零突变由构造保证。）

用法
----
  python nps_disasm.py <file.nps|目录> [-o 输出] [--encoding cp932] [--no-annot]
  python nps_disasm.py --verify <file|目录>      # 反汇编→汇编→逐字节比对
  拖放：把 .nps 或目录拖到本脚本上，生成同名 .asm.txt
"""
import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opcodelist as OC

TAG_RE = re.compile(r'<(/?)([A-Za-z_][A-Za-z0-9_]*)((?:\s+[^<>]*?)?)(/?)>')
ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
ATTR_RE2 = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^"\s<>]+)')
HTML_ONLY = {'HTML', 'HEAD', 'TITLE', 'BODY', 'PRE', 'meta', 'HR', 'META', 'html', 'BIG', 'B'}
UPPER = {k.upper(): k for k in OC.OPCODES}


def spec(name):
    """指令表查询（引擎大小写无关；返回规范化名的定义或 None）"""
    return OC.OPCODES.get(UPPER.get(name.upper(), name))


def strip_comment(line):
    """去掉行内 // 注释尾巴（用于标签校验/语义注释，不影响记录负载）"""
    s = line.strip(' \t')
    if s.startswith('//'):
        return ''
    i = s.find('//')
    return s[:i] if i >= 0 else s


# --------------------------------------------------------------------- 解析
def parse_tags(line):
    """返回 [(is_end, name, attrs_dict, raw)]，raw 为 <...> 原文。"""
    out = []
    for m in TAG_RE.finditer(line):
        is_end, name, body, _ = m.groups()
        attrs = {}
        for am in ATTR_RE.finditer(body or ''):
            attrs[am.group(1)] = am.group(2)
        for am in ATTR_RE2.finditer(body or ''):
            attrs.setdefault(am.group(1), am.group(2))
        out.append((bool(is_end), name, attrs, m.group(0)))
    return out


def classify(line):
    """行类型判定：BLK / CMT / TAG / HTM / TXT / MIX"""
    if line == '':
        return 'BLK'
    s = line.strip(' \t')
    if s == '':
        return 'TXT'
    if s.startswith('//'):
        return 'CMT'
    if s.startswith('<!'):
        return 'CMT'
    tags = parse_tags(line)
    stripped = TAG_RE.sub('', s).strip()
    # 去掉注释尾巴后再看有没有剩余可见文本
    stripped = re.sub(r'//.*$', '', stripped).strip()
    if tags and not stripped:
        names = {t[1] for t in tags}
        if names and names <= HTML_ONLY:
            return 'HTM'
        return 'TAG'
    if '<' in s:
        return 'MIX'
    return 'TXT'


def analyze(text):
    """对脚本正文做结构分析（标签/标签名/跳转/选择肢/名牌）。"""
    lines = text.split('\n')
    info = dict(tags={}, markers=[], jumps=[], choices=[], includes=[],
                names=set(), scene=None, unknown=[], inlinemark=set())
    for i, ln in enumerate(lines, 1):
        for is_end, name, attrs, raw in parse_tags(strip_comment(ln)):
            if spec(name) is None and name not in OC.INLINE:
                info['unknown'].append((i, name, ln.strip()[:100]))
            info['tags'][name] = info['tags'].get(name, 0) + 1
            if name == 'MARKER' or name == 'marker':
                nm = attrs.get('NAME') or attrs.get('name')
                if nm:
                    info['markers'].append(nm)
            if name == 'INCLUDE':
                if attrs.get('SRC'):
                    info['includes'].append(attrs['SRC'])
            if name == 'A':
                if attrs.get('HREF'):
                    info['jumps'].append(attrs['HREF'])
                if attrs.get('TEXT'):
                    info['choices'].append((attrs['TEXT'], attrs.get('HREF', '')))
            if name == 'CHOICE':
                if attrs.get('TEXT'):
                    info['choices'].append((attrs['TEXT'], attrs.get('HREF') or attrs.get('FILE', '')))
            if name in OC.NAME_SOURCE_TAGS:
                for a in OC.NAME_ATTRS:
                    if attrs.get(a):
                        info['names'].add(attrs[a])
        for m in re.finditer(r'<([A-Za-z][A-Za-z0-9_]*)>', ln):
            info['inlinemark'].add(m.group(1))
        if info['scene'] is None:
            m = re.search(r'<TITLE>\s*//(\S+)</TITLE>', ln)
            if m:
                info['scene'] = m.group(1)
    return info


def annotate(kind, line, tags, ctx):
    """为记录行生成语义注释（可能返回 None）。"""
    if kind not in ('TAG', 'MIX', 'HTM'):
        return None
    parts = []
    for is_end, name, attrs, raw in tags:
        sp = spec(name)
        if not sp or sp['kind'] in ('html',):
            continue
        if is_end:
            continue
        mn = sp['mnemonic']
        args = ' '.join(f'{k}={v}' for k, v in list(attrs.items())[:5])
        parts.append(f'{mn} {args}'.strip())
    if kind == 'MIX':
        txt = TAG_RE.sub('', re.sub(r'//.*$', '', line)).strip()
        if txt:
            parts.append(f'#TEXT "{txt[:60]}"' + ('…' if len(txt) > 60 else ''))
    return (' ; ' + ' | '.join(parts)) if parts else None


# --------------------------------------------------------------------- 主流程
def disasm_one(path, enc='cp932', annot=True, rel=None, out=None):
    raw = open(path, 'rb').read()
    text = raw.decode(enc, 'surrogateescape')
    # 换行统一性检查
    crlf = text.count('\r\n')
    lf = text.count('\n') - crlf
    if crlf and lf:
        eol = 'mixed'
    elif crlf:
        eol = 'crlf'
    else:
        eol = 'lf'
    body = text.replace('\r\n', '\n')
    final_nl = body.endswith('\n')
    lines = body[:-1].split('\n') if final_nl else body.split('\n')
    info = analyze(body)

    L = []
    L.append(';' + '=' * 78)
    L.append('; NPS 语义汇编 —— 由 tool/nps_disasm.py 生成；tool/nps_asm.py 可逐字节还原')
    L.append('; 引擎: NitroPlus "OC" 引擎 (脚本 charset=x-sjis)，本作「Hello,world.」')
    L.append(';' + '=' * 78)
    L.append(f'.file        {rel or os.path.basename(path)}')
    L.append(f'.encoding    {enc}')
    L.append(f'.eol         {eol}')
    L.append(f'.final_nl    {"yes" if final_nl else "no"}')
    L.append(f'.size        {len(raw)}')
    L.append(f'.sha1        {hashlib.sha1(raw).hexdigest()}')
    if info['scene']:
        L.append(f'.scene       {info["scene"]}')
    if info['includes']:
        L.append(f'.includes    {" ".join(sorted(set(info["includes"])))}')
    if info['markers']:
        L.append(f'.markers     {" ".join(info["markers"])}')
    if info['jumps']:
        L.append(f'.jumps       {" ".join(sorted(set(info["jumps"])))}')
    for txt, href in info['choices']:
        L.append(f'.choice      {href}  "{txt}"')
    if info['names']:
        L.append(f'.names       {" ".join(sorted(info["names"]))}')
    stats = {}
    for ln in lines:
        k = classify(ln)
        stats[k] = stats.get(k, 0) + 1
    L.append('.counts      ' + ' '.join(f'{k}={stats.get(k, 0)}' for k in
                                        ('TAG', 'MIX', 'TXT', 'CMT', 'HTM', 'BLK')))
    L.append('.tags        ' + ' '.join(f'{k}:{v}' for k, v in
                                        sorted(info['tags'].items(), key=lambda x: -x[1])))
    if info['unknown']:
        L.append('; !! 未登记标签（opcodelist.py 无定义）:')
        for i, nm, ctx in info['unknown'][:40]:
            L.append(f';     行 {i}: <{nm}>  {ctx}')
    L.append(';')
    L.append('; 记录格式： L<行号6位>  <KIND>  <原始行内容>      KIND=TAG/HTM/TXT/MIX/CMT/BLK')
    L.append('; 负载为原始行逐字节内容（不含换行）；注释行以 ; 开头，汇编器忽略')
    L.append(';' + '-' * 78)
    for i, ln in enumerate(lines, 1):
        kind = classify(ln)
        tags = parse_tags(ln)
        if annot and kind != 'BLK':
            a = annotate(kind, ln, tags, info)
            # 文本行/名牌/跳转/选择肢 才输出注释，控制体积
            interesting = (kind in ('TXT', 'MIX') or
                           any(t[1] in ('VOICE', 'voice', 'BUSTUP', 'CHOICE', 'A', 'MARKER',
                                        'marker', 'END', 'SYSTEM', 'LOAD', 'CALC') for t in tags))
            if a and interesting:
                L.append(';' + a)
        if kind == 'BLK':
            L.append(f'L{i:06d}  BLK')
        elif ln.rstrip() != ln or ln.strip(' \t') == '':
            # 首尾空白会被编辑器吞掉 → 引号化保存（负载经转义，见 opcodelist.esc）
            L.append(f'L{i:06d}  {"Q":<3} "{OC.esc(ln)}"')
        else:
            L.append(f'L{i:06d}  {kind:<3} {ln}')
    out = out or path + '.asm.txt'
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')
    return out, stats, info


def asm_one(asm_path, out_path, enc=None):
    meta, payloads = {}, []
    for ln in open(asm_path, encoding='utf-8').read().split('\n'):
        if ln.startswith(';'):
            continue
        if ln.startswith('.'):
            k = ln[1:].split(None, 1)
            meta[k[0]] = (k[1].strip() if len(k) > 1 else '')
            continue
        if not ln.strip():
            continue
        m = re.match(r'^L(\d{6})\s+(\w+)\s?(.*)$', ln)
        if not m:
            raise ValueError(f'无法解析的记录行: {ln[:80]!r}')
        idx, kind, payload = int(m.group(1)), m.group(2), m.group(3)
        if kind != 'BLK':
            payloads.append((idx, payload))
        else:
            payloads.append((idx, ''))
    lines = []
    n = 0
    for idx, payload in payloads:
        while n + 1 < idx:
            lines.append(None)          # 记录缺失行（理论不会发生）
            n += 1
        lines.append(payload)
        n = idx
    eol = {'crlf': '\r\n', 'lf': '\n'}.get(meta.get('eol', 'crlf'))
    if eol is None:
        raise ValueError('混合换行文件请用原始二进制编辑（本工具仅支持统一 CRLF/LF）')
    text = eol.join(l or '' for l in lines)
    if meta.get('final_nl', 'yes') == 'yes':
        text += eol
    enc = enc or meta.get('encoding', 'cp932')
    data = text.encode(enc)
    with open(out_path, 'wb') as f:
        f.write(data)
    return data


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('target', nargs='?')
    ap.add_argument('-o', '--output')
    ap.add_argument('--encoding', default='cp932')
    ap.add_argument('--no-annot', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    if len(sys.argv) == 1 or '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        return
    args = ap.parse_args()
    tgt = args.target
    if os.path.isdir(tgt):
        files = sorted(os.path.join(tgt, f) for f in os.listdir(tgt)
                       if f.lower().endswith(('.nps', '.h', '.ini')))
    else:
        files = [tgt]
    if args.verify:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import nps_asm
        bad = 0
        for f in files:
            a = f + '.asm.txt'
            disasm_one(f, args.encoding, not args.no_annot, out=a)
            orig = open(f, 'rb').read()
            rebuilt = nps_asm.asm_one(a, a + '.rebuild', args.encoding)
            if orig == rebuilt:
                print(f'[OK]   {os.path.basename(f)}  ({len(orig)} 字节)')
            else:
                bad += 1
                print(f'[FAIL] {os.path.basename(f)}  {len(orig)} -> {len(rebuilt)}')
        print(f'--- 反汇编/汇编往返: {len(files)-bad}/{len(files)} 零突变')
        sys.exit(1 if bad else 0)
    tot = {}
    for f in files:
        out, stats, info = disasm_one(f, args.encoding, not args.no_annot, out=(
            args.output if (args.output and not os.path.isdir(tgt)) else None))
        for k, v in stats.items():
            tot[k] = tot.get(k, 0) + v
        print(f'[OK] {f} -> {out}')
        if info['unknown']:
            print(f'     !! 未登记标签 {len(info["unknown"])} 处: ' +
                  ' '.join(sorted({u[1] for u in info['unknown']})))
            if args.strict:
                sys.exit(1)
    print('行类型合计:', tot)


if __name__ == '__main__':
    main()
