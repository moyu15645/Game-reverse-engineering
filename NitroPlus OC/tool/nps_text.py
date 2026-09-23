#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nps_text.py —— .nps 文本提取 / 写回（本地化核心工具）
================================================================================
提取：.nps → xxx.json（翻译用，遵循工作备忘录 JSON 规范）+ xxx.json.meta.json（注入用）
写回：xxx.nps + xxx.json (+ meta) → 译文 .nps

提取范围（可开关）
------------------
  dialog    带名牌的台词（名牌来自 <VOICE NAME="…"> / <box name="…">）
  narration 旁白/叙述（无名牌）
  choice    <CHOICE TEXT="…"> / <A TEXT="…"> 选项文字
  ruby      <R TEXT="…"> 注音文字
  calc      <CALC OPERATOR="…"> 中出现的非数值文本（日期、旗帜名等）
  comment   开发者注释（默认不提取，--with-comments 打开）

不提取：脚本跳转（<A HREF>）、资源名（SRC/BODY/FILE）、纯 ASCII 文本（可用 --keep-ascii 打开）

显示文本的界定
--------------
  行内 // 之后为注释（引擎不显示）；<…> 为命令/行内记号（不显示）；
  <K>/<k> 为消息内换行记号。因此：显示文本 = 首个“标签外的 //”之前、剔除 <…> 之后的内容。

用法
----
  # 提取（目录批处理）
  python nps_text.py extract unpack/script/script -o text/script
  python nps_text.py extract x.nps -o out_dir [--split-k|--merge-k] [--with-comments] [--keep-ascii]

  # 写回
  python nps_text.py inject unpack/script/script text/script -o out/script
        [--name-map names.json] [--linebytes 50] [--no-kalign] [--dry-run]

  # 自检：未改动 JSON 写回必须与原文件逐字节一致
  python nps_text.py selftest unpack/script/script

  --linebytes N : 译文按 N 个 cp932 字节断行（插入 <K>），遵守 system.ini 的禁则表，
                  且「」开头结尾的对话行在断行后补全角空格（备忘录 方案B3.2）
  --split-k     : 提取时把 <K> 拆成多条 entry（id 相邻），写回按原 <K> 位置合并
  --merge-k     : 提取时删掉行内 <K> 合成一整句，写回按译文整句替换
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opcodelist as OC

TAG_OR_MARK = re.compile(r'<[^<>]*>')
# 行内“不显示”的记号（其余标签一律视为命令）
MARKERS_NODISP = {'K', 'k', 'clear', 'CLEAR', 'CLAER', 'HR', 'SELECT', 'Hidemessage',
                  'wait', 'BGM', 'FLASH', 'LOAD', 'PRE', 'HEAD', 'TITLE', 'HTML'}

def _rel(path, root):
    """相对给定根目录的路径（用于镜像包内目录结构）"""
    if os.path.isdir(root):
        return os.path.relpath(path, root).replace('\\', '/')
    return os.path.basename(path)


JP_RE = re.compile(r'[\u3040-\u30ff\u30a0-\u30ff\u4e00-\u9fff\uff66-\uff9f\u3005\u3006]')
# 拉丁字母（半角/全角）：纯英文界面行（如 now connecting....）同样需要翻译
LATIN_RE = re.compile(r'[A-Za-z\uff21-\uff3a\uff41-\uff5a]')
# 标签残片特征：裸尖括号、或 属性="值" 形态 → 属解析残留，不作为文本提取
_TAGLIKE = re.compile(r'[<>]|=\s*"')


def is_text(disp):
    """disp 是否值得提取：含假名/汉字；或含拉丁字母且不是标签残片。
    纯标点 / 纯数字（‥‥、――、2020年 之类）不算文本。"""
    if JP_RE.search(disp):
        return True
    return bool(LATIN_RE.search(disp) and not _TAGLIKE.search(disp))


# ------------------------------------------------------------------ 基础工具
def split_line(line):
    """把一行拆成 (prefix, text_span, suffix)：
    prefix/suffix 为不显示的结构部分（标签、注释、首尾空白），text_span 为显示文本区。
    """
    # 找标签外的 // 注释起点
    i, n, cut = 0, len(line), len(line)
    while i < n:
        if line[i] == '<':
            j = line.find('>', i)
            i = n if j < 0 else j + 1
            continue
        if line.startswith('//', i):
            cut = i
            break
        i += 1
    body = line[:cut]
    suffix = line[cut:]
    # 显示文本区 = 首个/末个非标签字符之间
    # 【行首标签】整段跳过：<K>正文 / <se …>正文 / <R TEXT="…">正文</R>
    #   旧实现直接对整行取“首个非 <> 字符”，会把标签名（K、se…）误当正文起点，
    #   于是调用方只能靠 `行首是 < 就整行跳过` 兜底 —— 那会漏掉 <K> 开头的对白。
    s0 = 0
    while s0 < len(body) and body[s0] == '<':
        j = body.find('>', s0)
        if j < 0:
            # 标签未闭合：引擎把长标签折成了多行（如 `<BUSTUP … ALIG` + `N="right" …>`），
            # 整行都属于结构部分、没有可显示正文 → 不提取，
            # 否则标签里的资源名（BODY/FACE/SRC）会被当成对白送去翻译，注入后立绘/音效全废
            return line, '', ''
        s0 = j + 1
    m = re.search(r'[^<>]', body[s0:])
    if not m:
        return line, '', ''
    start = s0 + m.start()
    # 末个非标签字符：反向扫描，跳过标签
    end = len(body)
    while end > start and body[end - 1] == '>':
        j = body.rfind('<', start, end)
        if j < 0:
            break
        end = j
    while end > start and body[end - 1] in '<>':
        end -= 1
    mid = body[start:end]
    return body[:start], mid, body[end:] + suffix


def display_text(span):
    """去掉行内记号后的可显示文本"""
    return TAG_OR_MARK.sub('', span).strip()


def _tag_name(t):
    m = re.match(r'</?\s*([A-Za-z0-9_]*)', t)
    return m.group(1).upper() if m else t


def restore_tags(msg, src_span):
    """★ 注入前用【原文】的标签整体覆盖译文里的标签。

    标签内部是资源名 / 数值 / 属性定界符，绝不允许被翻译或字形映射：
      · `<se SRC="薫カツサンド飲み込む5">` 资源名被译 → 音效找不到文件
      · `<BUSTUP NAME="奈都美KW" BODY="私服驚き弱">` 立绘名被译 → 立绘不显示
      · `<wait time="5">` 引号被写成全角 → 引擎直接忽略该标签
    仅在「标签数量相同、标签名序列一致」时整体还原；否则原样返回（由调用方告警）。

    需要翻译的属性值（`<VOICE NAME=…>` 角色名、`<CHOICE TEXT=…>` 选项、`<R TEXT=…>` 注音）
    都已作为独立条目单独提取并单独注入，故整体还原不会漏译任何内容。

    返回 (新串, 被还原的标签数, 是否可还原)
    """
    src = src_span or ''
    # ① 宏调用（标签名非 ASCII，如 <主画面通常閉じる２>）单独保护：
    #    模型经常把它整段删掉 → 主画面/演出指令丢失。
    #    数量一致时按位置换回原文写法；数量不足时按原文中的相对位置补插
    #    （落在标点之后，避免切断词）。
    macros = [t for t in TAG_OR_MARK.findall(src)
              if not re.match(r'</?[A-Za-z0-9_]', t)]
    if macros:
        cur = [t for t in TAG_OR_MARK.findall(msg)
               if not re.match(r'</?[A-Za-z0-9_]', t)]
        if len(cur) == len(macros):
            cnt = [0]

            def _rm(mo):
                t = mo.group(0)
                if re.match(r'</?[A-Za-z0-9_]', t):
                    return t
                i = cnt[0]
                cnt[0] += 1
                return macros[i]

            msg = TAG_OR_MARK.sub(_rm, msg)
        elif len(cur) < len(macros):
            for t in macros[len(cur):]:
                pos = src.find(t)
                ratio = (pos / len(src)) if (src and pos >= 0) else 0.5
                k = int(len(msg) * ratio)
                ins = len(msg)
                for j in range(k, len(msg)):
                    if msg[j] in '，。、！？…,.':
                        ins = j + 1
                        break
                msg = msg[:ins] + t + msg[ins:]
    p = TAG_OR_MARK.findall(src)
    m = TAG_OR_MARK.findall(msg)
    if not p:
        return msg, 0, True
    if len(p) != len(m) or [_tag_name(x) for x in p] != [_tag_name(x) for x in m]:
        return msg, 0, False
    cnt = [0]

    def _r(mo):
        i = cnt[0]
        cnt[0] += 1
        return p[i]

    return TAG_OR_MARK.sub(_r, msg), sum(1 for a, b in zip(p, m) if a != b), True


def inline_marks(span):
    return [m.group(0) for m in re.finditer(r'<[^<>]*>', span)]


def is_disp_tag(raw):
    name = re.match(r'</?([A-Za-z0-9_]*)', raw)
    nm = name.group(1) if name else ''
    return nm not in MARKERS_NODISP and nm.upper() not in {x.upper() for x in MARKERS_NODISP}


# ------------------------------------------------------------------ 提取
NAME_TAGS = {'VOICE', 'voice', 'BOX', 'box', 'BUSTUP', 'bustup'}


def extract_file(path, rel, split_k=False, merge_k=False, with_comments=False,
                 keep_ascii=False, name_strategy='voice-box'):
    raw = open(path, 'rb').read()
    text = raw.decode('cp932', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')
    entries, metas = [], []
    cur_name, cur_name_src, dev_speaker = None, None, None
    for i, ln in enumerate(lines, 1):
        s = ln.strip(' \t')
        if s.startswith('//'):
            m = re.match(r'^//\s*【([^】]*)】', s)
            if m:
                dev_speaker = m.group(1)
            if with_comments and len(s) > 2:
                disp = s[2:]
                if JP_RE.search(disp):
                    entries.append(dict(id=len(entries), pre_jp=disp, message=disp))
                    metas.append(dict(id=entries[-1]['id'], line_no=i, kind='comment',
                                      prefix='//', suffix='', name_line=None, name_tag=None,
                                      orig_message=disp, inline=[]))
            continue
        if s == '':
            continue
        # 名牌来源（行首标签只做“名牌/清屏”副作用，不能因此把整行正文丢掉）
        if s.startswith('<'):
            for m in re.finditer(r'<(VOICE|voice|BOX|box|BUSTUP|bustup)\b([^<>]*)>', s):
                tag, body = m.group(1), m.group(2)
                am = re.search(r'\b(NAME|name)\s*=\s*"([^"]*)"', body)
                if am:
                    nm = am.group(2)
                    if tag.upper() == 'BUSTUP' and name_strategy == 'voice-box':
                        continue
                    if nm:
                        cur_name, cur_name_src = nm, dict(line_no=i, tag=tag, attr=am.group(1))
            if re.match(r'^<\s*(clear|CLEAR|CLAER)\b', s):
                cur_name, cur_name_src = None, None
        # 文本行（行首标签已由 split_line 归入 prefix；去掉标签后无显示文本才跳过）
        prefix, span, suffix = split_line(ln)
        disp = display_text(span)
        if not disp:
            continue
        # 跳转标签 <A HREF="…">文字</A> 里的“显示文字”是开发期分支标记
        # （#共通、千絵梨好意有…），游戏运行时不显示 → 不作为文本提取
        # 注：`<A HREF="…">跳转显示文本</A>` 与 `\t310.nps:` 调试行虽不是玩家可见文本，
        # 但【不能】在这里过滤 —— 一旦少产出条目，新增条目的 id 会整体前移，
        # 已翻译好的译文就会全部错位。这类“不该翻”的条目改由 nps_refill.py
        # 在生成待翻清单时排除：id 照旧保留，只是不进补翻包。
        if not keep_ascii and not is_text(disp):
            continue
        marks = [m.group(0) for m in re.finditer(r'<[^<>]*>', span)]
        body = span if not (merge_k or split_k) else disp
        parts_info = None
        if merge_k or split_k:
            seps = list(re.finditer(r'<[Kk]>', span))
            if seps:
                parts_info = [p for p in re.split(r'<[Kk]>', disp)]
        ent = {}
        if cur_name:
            ent['name'] = cur_name
        if split_k and parts_info and len(parts_info) > 1:
            first_id = len(entries)
            for pi, part in enumerate(parts_info):
                if not part:
                    continue
                e = dict(id=len(entries))
                if cur_name and pi == 0:
                    e['name'] = cur_name
                e['pre_jp'] = part
                e['message'] = part
                entries.append(e)
                metas.append(dict(id=e['id'], line_no=i, kind='dialog' if cur_name else 'narration',
                                  prefix=prefix, suffix=suffix, name_line=(cur_name_src or {}).get('line_no'),
                                  name_tag=(cur_name_src or {}).get('tag'),
                                  name_attr=(cur_name_src or {}).get('attr'),
                                  orig_name_raw=cur_name, orig_message=disp, orig_span=span,
                                  inline=marks, dev_speaker=dev_speaker, part_index=pi,
                                  part_of=first_id, k_join=True))
        else:
            ent['id'] = len(entries)
            ent['pre_jp'] = body
            ent['message'] = body
            entries.append(ent)
            metas.append(dict(id=ent['id'], line_no=i, kind='dialog' if cur_name else 'narration',
                              prefix=prefix, suffix=suffix,
                              name_line=(cur_name_src or {}).get('line_no'),
                              name_tag=(cur_name_src or {}).get('tag'),
                              name_attr=(cur_name_src or {}).get('attr'),
                              orig_name_raw=cur_name, orig_message=disp, orig_span=span,
                              inline=marks, dev_speaker=dev_speaker,
                              k_parts=(parts_info if (merge_k and parts_info and len(parts_info) > 1) else None)))
        dev_speaker = None
    # 选择肢 / 注音 / CALC：单独扫描
    for i, ln in enumerate(lines, 1):
        s = ln.strip(' \t')
        if s.startswith('//'):
            continue
        for m in re.finditer(r'<(CHOICE|choice|A|a)\b([^<>]*)>', s):
            tag, body = m.group(1), m.group(2)
            am = re.search(r'\bTEXT\s*=\s*"([^"]*)"', body, re.I)
            if am and am.group(1).strip() and JP_RE.search(am.group(1)):
                v = am.group(1)
                e = dict(id=len(entries), pre_jp=v, message=v)
                entries.append(e)
                metas.append(dict(id=e['id'], line_no=i, kind='choice', prefix='', suffix='',
                                  name_line=None, name_tag=tag, name_attr='TEXT',
                                  orig_message=v, inline=[], attr_raw=m.group(0)))
        for m in re.finditer(r'<(R|r)\b([^<>]*)>', s):
            am = re.search(r'\bTEXT\s*=\s*"([^"]*)"', m.group(2), re.I)
            if am and am.group(1).strip():
                v = am.group(1)
                e = dict(id=len(entries), pre_jp=v, message=v)
                entries.append(e)
                metas.append(dict(id=e['id'], line_no=i, kind='ruby', prefix='', suffix='',
                                  name_line=None, name_tag=m.group(1), name_attr='TEXT',
                                  orig_message=v, inline=[], attr_raw=m.group(0)))
        for m in re.finditer(r'<(CALC|calc)\b([^<>]*)>', s):
            am = re.search(r'\b(OPERATOR|operator)\s*=\s*"([^"]*)"', m.group(2))
            if am and am.group(2).strip() and JP_RE.search(am.group(2)) and not am.group(2).isdigit():
                v = am.group(2)
                e = dict(id=len(entries), pre_jp=v, message=v)
                entries.append(e)
                metas.append(dict(id=e['id'], line_no=i, kind='calc', prefix='', suffix='',
                                  name_line=None, name_tag=m.group(1), name_attr=am.group(1),
                                  orig_message=v, inline=[], attr_raw=m.group(0)))
    return entries, dict(file=rel, encoding='cp932', size=len(raw),
                         sha1=__import__('hashlib').sha1(raw).hexdigest(),
                         eol='crlf', entries=metas)


def cmd_extract(a):
    tgt = a.target
    files = ([os.path.join(tgt, f) for f in sorted(os.listdir(tgt))]
             if os.path.isdir(tgt) else [tgt])
    os.makedirs(a.output, exist_ok=True)
    nf = ne = 0
    for f in files:
        if not f.lower().endswith(('.nps', '.h')):
            continue
        rel = _rel(f, tgt)
        ents, meta = extract_file(f, rel, a.split_k, a.merge_k, a.with_comments,
                                  a.keep_ascii, a.name_strategy)
        if not ents:
            print(f'[SKIP] {f} —— 0 条对白')
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        sub = os.path.join(a.output, os.path.dirname(rel))
        os.makedirs(sub, exist_ok=True)
        jp = os.path.join(sub, stem + '.json')
        mp = jp + '.meta.json'
        with open(jp, 'w', encoding='utf-8') as fo:
            json.dump(ents, fo, ensure_ascii=False, indent=1)
        with open(mp, 'w', encoding='utf-8') as fo:
            json.dump(meta, fo, ensure_ascii=False, indent=1)
        nf += 1
        ne += len(ents)
        print(f'[OK] {os.path.basename(f):28s} {len(ents):5d} 条 -> {jp}')
    print(f'--- 共 {nf} 个文件 / {ne} 条文本')


# ------------------------------------------------------------------ 写回
def load_jinmei(p):
    if not p:
        return {}
    d = json.load(open(p, encoding='utf-8'))
    return d.get('names', d) if isinstance(d, dict) else {}


def jinmei_mapping(d):
    """把 {原文: 译文} 表转成“注入后的名字也过一遍映射”的等价映射
    （备忘录取向：名字以输出形态出现，故映射要作用在译文上）"""
    return d


def load_kinzoku(ini_dir):
    """从 system.ini / backlog.ini 读取禁则表"""
    start = end = ''
    for nm in ('system.ini', 'backlog.ini'):
        p = os.path.join(ini_dir, nm) if ini_dir else None
        if p and os.path.exists(p):
            t = open(p, 'rb').read().decode('cp932', 'replace')
            for key in ('start', 'end'):
                m = re.search(rf'^{key}\s*=\s*(.+)$', t, re.M)
                if m and not (key == 'start' and start):
                    if key == 'start':
                        start = start or m.group(1).strip()
                    else:
                        end = end or m.group(1).strip()
    return set(start), set(end)


def break_text(txt, width, start_set, end_set, is_dialog):
    """按 cp932 字节数插入 <K> 断行（备忘录 方案B3.2）
       · 宽度按 cp932 字节计（全角字符 = 2 字节），全角空格同样计入
       · 遵守禁则：行尾禁则字符移到下一行；行首禁则字符从上一行借入
       · 以「『 开头、以 」』 结尾的对话行，断行后行首补一个全角空格
    """
    def blen(t):
        return len(t.encode('cp932'))

    if blen(txt) <= width:
        return txt
    lines, cur = [], ''
    for ch in txt:
        if cur and blen(cur) + blen(ch) > width:
            moved, guard = '', 0
            while cur and cur[-1] in end_set and guard < 4:
                moved, cur, guard = cur[-1] + moved, cur[:-1], guard + 1
            if ch in start_set and cur and guard < 4:
                moved, cur = cur[-1] + moved, cur[:-1]
            lines.append(cur)
            cur = ('\u3000' if is_dialog else '') + moved + ch
        else:
            cur += ch
    lines.append(cur)
    return '<K>'.join(lines)


def cmd_inject(a):
    src_dir, json_dir = a.target, a.json_dir
    name_map = jinmei_mapping(load_jinmei(a.name_map))
    kstart, kend = load_kinzoku(a.ini_dir)
    files = ([os.path.join(src_dir, f) for f in sorted(os.listdir(src_dir))]
             if os.path.isdir(src_dir) else [src_dir])
    os.makedirs(a.output, exist_ok=True)
    stat = dict(src=0, ent=0, changed=0, same=0, name_changed=0, skipped=0, err=0, warn=0,
                tag_restored=0, tag_conflict=0)
    for f in files:
        if not f.lower().endswith(('.nps', '.h')):
            continue
        rel = _rel(f, src_dir)
        stem = os.path.splitext(os.path.basename(f))[0]
        sub = os.path.join(json_dir, os.path.dirname(rel))
        jp = os.path.join(sub, stem + '.json')
        if not os.path.exists(jp):
            stat['skipped'] += 1
            continue
        ents = json.load(open(jp, encoding='utf-8'))
        meta = json.load(open(jp + '.meta.json', encoding='utf-8'))
        raw = open(f, 'rb').read()
        text = raw.decode('cp932', 'replace')
        eol = '\r\n' if '\r\n' in text else '\n'
        lines = text.split('\n')
        lines = [l[:-1] if l.endswith('\r') else l for l in lines]
        mmap = {m['id']: m for m in meta['entries']}
        # 先处理“属性级”条目（choice/ruby/calc/comment），再处理整行正文，
        # 否则同一行上整行替换会先抹掉 <R TEXT=…> 等属性，导致属性无法改写
        _ATTR_KINDS = ('choice', 'ruby', 'calc', 'comment')
        ents = sorted(ents, key=lambda e: (0 if mmap.get(e['id'], {}).get('kind') in _ATTR_KINDS
                                           else 1, e['id']))
        for e in ents:
            m = mmap.get(e['id'])
            if not m:
                continue
            idx = m['line_no'] - 1
            if idx >= len(lines):
                print(f'[错误] {f}: meta 行号越界 {m["line_no"]}')
                stat['err'] += 1
                continue
            orig = lines[idx]
            newmsg = e.get('message', m.get('orig_message', ''))
            newname = e.get('name')
            try:
                newmsg.encode('cp932')
            except UnicodeEncodeError as ex:
                print(f'[错误] {f}:{m["line_no"]} 译文无法用 cp932 编码: '
                      f'{newmsg[ex.start:ex.start+1]!r} (位置 {ex.start})  id={e["id"]}')
                stat['err'] += 1
                continue
            if m['kind'] in ('choice', 'ruby', 'calc', 'comment'):
                if newmsg == m['orig_message']:
                    stat['same'] += 1
                    continue
                attrs = {'choice': 'TEXT', 'ruby': 'TEXT', 'calc': None, 'comment': None}
                if m['kind'] == 'comment':
                    lines[idx] = '//' + newmsg
                else:
                    attr = m.get('name_attr') or attrs[m['kind']]
                    tag = m['name_tag']
                    pat = re.compile(rf'(<{tag}\b[^<>]*?\b{attr}\s*=\s*")([^"]*)(")', re.I)
                    if not pat.search(lines[idx]):
                        # 译文把该内嵌标签整段丢掉/改写了（LLM 常见），此处无法注入，
                        # 记警告并保留原文属性（不影响其它行）
                        stat['warn'] += 1
                        if stat['warn'] <= 8:
                            print(f'[警告] {f}:{m["line_no"]} 译文里已无 {tag}.{attr} 属性，'
                                  f'该属性保留原文（原文：{m["orig_message"][:24]!r}）')
                        continue
                    lines[idx] = pat.sub(lambda x: x.group(1) + newmsg + x.group(3), lines[idx], count=1)
                stat['changed'] += 1
                continue
            # 文本行
            prefix, span, suffix = split_line(orig)
            if (newmsg == m.get('orig_span', m['orig_message'])
                    and not (newname and newname != m.get('orig_name_raw'))):
                stat['same'] += 1
                continue
            msg = newmsg
            # ★ 标签一律用【原文】覆盖：资源名/数值/引号不容翻译或字形映射改动
            msg, ntag, ok = restore_tags(msg, m.get('orig_span', ''))
            stat['tag_restored'] += ntag
            if not ok:
                stat['tag_conflict'] += 1
                if stat['tag_conflict'] <= 8:
                    print(f'[警告] {f}:{m["line_no"]} 译文标签与原文数量/名称不符，'
                          f'保留译文原样  id={e["id"]}')
            if a.linebytes:
                is_dialog = msg.startswith(('「', '『')) and msg.endswith(('」', '』'))
                msg = break_text(msg, a.linebytes, kstart, kend, is_dialog)
            lines[idx] = prefix + msg + suffix
            stat['changed'] += 1
            # 名牌改写
            if newname and newname != m.get('orig_name_raw') and m.get('name_line'):
                nidx = m['name_line'] - 1
                tag, attr = m.get('name_tag'), m.get('name_attr') or 'NAME'
                pat = re.compile(rf'(<{tag}\b[^<>]*?\b{attr}\s*=\s*")([^"]*)(")', re.I)
                if pat.search(lines[nidx]):
                    try:
                        newname.encode('cp932')
                    except UnicodeEncodeError:
                        print(f'[错误] {f}:{m["name_line"]} 译名无法 cp932 编码: {newname!r}')
                        stat['err'] += 1
                        continue
                    lines[nidx] = pat.sub(lambda x: x.group(1) + newname + x.group(3),
                                          lines[nidx], count=1)
                    stat['name_changed'] += 1
        out = eol.join(lines)
        if raw.endswith(b'\r\n') and not out.endswith(eol):
            out += eol
        elif not raw.endswith(b'\n') and out.endswith(eol):
            out = out[:-len(eol)]
        outrel = os.path.join(a.output, os.path.dirname(rel))
        os.makedirs(outrel, exist_ok=True)
        op = os.path.join(outrel, os.path.basename(f))
        with open(op, 'wb') as fo:
            fo.write(out.encode('cp932'))
        stat['src'] += 1
        stat['ent'] += len(ents)
    print(f"--- 处理 {stat['src']} 文件 / {stat['ent']} 条：改写 {stat['changed']}，"
          f"未改动 {stat['same']}，名牌改写 {stat['name_changed']}，"
          f"跳过文件 {stat['skipped']}，错误 {stat['err']}，警告 {stat['warn']}，"
          f"标签还原 {stat['tag_restored']}，标签冲突 {stat['tag_conflict']}")
    return stat


def cmd_selftest(a):
    """未改动 JSON → 写回 → 与原文件逐字节比对"""
    import tempfile, subprocess
    tgt = a.target
    files = ([os.path.join(tgt, f) for f in sorted(os.listdir(tgt))]
             if os.path.isdir(tgt) else [tgt])
    tmp = tempfile.mkdtemp()
    bad = 0
    for f in files:
        if not f.lower().endswith(('.nps', '.h')):
            continue
        rel = _rel(f, tgt)
        ents, meta = extract_file(f, rel, a.split_k, a.merge_k, a.with_comments,
                                  a.keep_ascii, a.name_strategy)
        stem = os.path.splitext(os.path.basename(f))[0]
        sub = os.path.join(tmp, os.path.dirname(rel))
        os.makedirs(sub, exist_ok=True)
        jp = os.path.join(sub, stem + '.json')
        json.dump(ents, open(jp, 'w', encoding='utf-8'), ensure_ascii=False)
        json.dump(meta, open(jp + '.meta.json', 'w', encoding='utf-8'), ensure_ascii=False)
    ns = argparse.Namespace(target=tgt, json_dir=tmp, output=os.path.join(tmp, '_out'),
                            name_map=None, linebytes=None, ini_dir=a.ini_dir, dry_run=True)
    cmd_inject(ns)
    n = 0
    for f in files:
        if not f.lower().endswith(('.nps', '.h')):
            continue
        rel = _rel(f, tgt)
        op = os.path.join(tmp, '_out', os.path.dirname(rel), os.path.basename(f))
        if not os.path.exists(op):
            continue
        n += 1
        if open(f, 'rb').read() != open(op, 'rb').read():
            bad += 1
            print(f'[FAIL] {os.path.basename(f)}')
    print(f'--- 空注入自检: {n-bad}/{n} 与原文件逐字节一致')
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('cmd', nargs='?')
    ap.add_argument('target', nargs='?')
    ap.add_argument('json_dir', nargs='?')
    ap.add_argument('-o', '--output')
    ap.add_argument('--split-k', action='store_true')
    ap.add_argument('--merge-k', action='store_true')
    ap.add_argument('--with-comments', action='store_true')
    ap.add_argument('--keep-ascii', action='store_true')
    ap.add_argument('--name-strategy', default='voice-box', choices=['voice-box', 'all'])
    ap.add_argument('--name-map')
    ap.add_argument('--linebytes', type=int)
    ap.add_argument('--ini-dir', default='unpack/system')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    if len(sys.argv) == 1 or '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        return
    a = ap.parse_args()
    if a.cmd == 'extract':
        cmd_extract(a)
    elif a.cmd == 'inject':
        cmd_inject(a)
    elif a.cmd == 'selftest':
        cmd_selftest(a)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
