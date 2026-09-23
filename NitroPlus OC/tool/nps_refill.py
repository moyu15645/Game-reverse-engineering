#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nps_refill.py —— 增量补漏提取（保持既有译文可用）
================================================================================
修复 nps_text.py 的“行首 <K> 整行被跳过”缺陷后，重新提取会多出上千条文本。
若直接覆盖，新条目按行序插入会让**所有既有 id 后移**，已翻译好的 output 就全部错位。

本工具的做法：以 (line_no, kind, pre_jp) 为配对键，把新提取结果与既有提取对齐 ——
  · 配对上的条目        → 沿用【旧 id】，内容原样不动
  · 新提取多出来的条目  → 作为新增，id 从 max(旧 id)+1 开始顺延，排到文件末尾
于是：
  · 旧译文（output）完全不用重翻；
  · 只需补翻 --gap 输出的新增条目，按 id 合并回去即可。

用法
----
  python tool/nps_refill.py <src_dir> <old_dir> -o <out_dir> [--gap <gap_dir>]

  <src_dir>   .nps / .h 所在解包目录（如 unpack/script/script）
  <old_dir>   既有提取目录（含 <stem>.json 与 <stem>.json.meta.json）
  -o          输出目录：旧条目（id 不变）+ 新增条目（id 顺延）
  --gap       只含新增条目的“补翻包”，格式与 <old_dir> 的 json 一致，可直接送翻译

退出码非 0 表示存在无法配对的旧条目（提取逻辑对既有内容产生了变化，须人工确认）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nps_text as T


def _key(m, e):
    return (m['line_no'], m['kind'], e['pre_jp'])


# 这些条目会被提取（以保证 id 稳定），但**不该翻**，因此不进待翻包：
#   · <A HREF="#共通">共通</A>  —— 内部跳转标签的显示文字，是开发期分支标记
#   · "\t310.nps:"            —— 开发期章节定位标记
HREF_RE = re.compile(r'\bHREF\s*=', re.I)
DEBUG_MARK_RE = re.compile(r'\s*\d+\.nps:\s*$')


def is_untranslatable(ent, meta_by_id):
    """结构标记类条目：保留 id、但不需要翻译"""
    m = meta_by_id.get(ent['id'], {})
    if HREF_RE.search((m.get('prefix') or '') + (m.get('suffix') or '')):
        return True
    return bool(DEBUG_MARK_RE.match(ent.get('pre_jp') or ''))


def _dump(obj, path):
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)


def refill(src, old, out, gap=None, quiet=False):
    files = [f for f in sorted(os.listdir(src)) if f.lower().endswith(('.nps', '.h'))]
    os.makedirs(out, exist_ok=True)
    if gap:
        os.makedirs(gap, exist_ok=True)
    stat = dict(files=0, old=0, kept=0, new=0, missing=0, noold=0, ignored=0)
    problems = []
    gap_stat = []
    for fn in files:
        f = os.path.join(src, fn)
        stem = os.path.splitext(fn)[0]
        ents, meta = T.extract_file(f, fn)
        old_jp = os.path.join(old, stem + '.json')
        old_mp = old_jp + '.meta.json'
        new_ids = []
        if not (os.path.exists(old_jp) and os.path.exists(old_mp)):
            stat['noold'] += 1
            out_ents = [dict(e, id=i) for i, e in enumerate(ents)]
            out_metas = [dict(m, id=i) for i, m in enumerate(meta['entries'])]
            new_ids = [e['id'] for e in out_ents]
            if not quiet:
                print(f'  [新文件] {fn}: {len(out_ents)} 条（无旧提取，全部视为首提）')
        else:
            oents = json.load(open(old_jp, encoding='utf-8'))
            om = {m['id']: m for m in json.load(open(old_mp, encoding='utf-8'))['entries']}
            stat['old'] += len(oents)
            idx = {}
            for i, (m, e) in enumerate(zip(meta['entries'], ents)):
                idx.setdefault(_key(m, e), []).append(i)
            pairs, taken = {}, set()
            for e in oents:
                m = om.get(e['id'])
                if m is None:
                    problems.append((stem, e['id'], '旧 meta 缺该 id'))
                    stat['missing'] += 1
                    continue
                k = _key(m, e)
                cand = [i for i in idx.get(k, []) if i not in taken]
                if not cand:
                    problems.append((stem, e['id'],
                                     f'新提取找不到 {k[0]}:{k[1]} {e["pre_jp"][:34]!r}'))
                    stat['missing'] += 1
                    continue
                taken.add(cand[0])
                pairs[cand[0]] = e['id']
                stat['kept'] += 1
            nid = (max(e['id'] for e in oents) + 1) if oents else 0
            out_ents, out_metas = [], []
            for i in range(len(ents)):
                if i in pairs:
                    out_ents.append(dict(ents[i], id=pairs[i]))
                    out_metas.append(dict(meta['entries'][i], id=pairs[i]))
                else:
                    out_ents.append(dict(ents[i], id=nid))
                    out_metas.append(dict(meta['entries'][i], id=nid))
                    new_ids.append(nid)
                    nid += 1
                    stat['new'] += 1
            # 被丢弃的旧条目（配对失败）也要保留，避免静默丢内容
            for e in oents:
                if e['id'] not in set(pairs.values()):
                    out_ents.append(e)
                    out_metas.append(om[e['id']])
        out_ents.sort(key=lambda e: e['id'])
        out_metas.sort(key=lambda m: m['id'])
        _dump(out_ents, os.path.join(out, stem + '.json'))
        _dump(dict(meta, entries=out_metas),
              os.path.join(out, stem + '.json.meta.json'))
        if gap and new_ids:
            ids = set(new_ids)
            mb = {m['id']: m for m in out_metas}
            todo = [e for e in out_ents
                    if e['id'] in ids and not is_untranslatable(e, mb)]
            stat['ignored'] += len(ids) - len(todo)
            gp = os.path.join(gap, stem + '.json')
            if todo:
                _dump(todo, gp)
                gap_stat.append((stem, len(todo), min(x['id'] for x in todo)))
            elif os.path.exists(gp):
                os.remove(gp)
        stat['files'] += 1
    print(f"--- 处理 {stat['files']} 文件：沿用旧 id {stat['kept']}，新增 {stat['new']}，"
          f"无法配对 {stat['missing']}，无旧提取 {stat['noold']}；"
          f"其中结构标记类 {stat['ignored']} 条已排除出待翻包（id 仍保留）")
    if gap_stat:
        tot = sum(x[1] for x in gap_stat)
        print(f"--- 补翻包：{len(gap_stat)} 文件 / {tot} 条 "
              f"（最大新增文件：{max(gap_stat, key=lambda x: x[1])[0]} "
              f"{max(x[1] for x in gap_stat)} 条）")
    if problems:
        print(f'\n[!] {len(problems)} 条旧条目无法与新提取配对（已原样保留，未丢内容）：')
        for p in problems[:20]:
            print(f'    {p[0]} id={p[1]}: {p[2]}')
    return stat, problems


def main():
    ap = argparse.ArgumentParser(description='增量补漏提取（保持既有 id 不变）')
    ap.add_argument('src_dir', help='.nps/.h 解包目录')
    ap.add_argument('old_dir', help='既有提取目录（含 .json 与 .json.meta.json）')
    ap.add_argument('-o', '--output', required=True, help='输出目录')
    ap.add_argument('--gap', help='只含新增条目的补翻包目录')
    a = ap.parse_args()
    stat, problems = refill(a.src_dir, a.old_dir, a.output, a.gap)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
