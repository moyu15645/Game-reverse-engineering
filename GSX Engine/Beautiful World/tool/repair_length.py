#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair_length.py — 让超长译文重新压缩到固定槽位预算之内（方案 A）

    每条文本的字节预算：
        budget_bytes = slot - 1 - len(prefix) - len(suffix)
    实际占用：
        len(from_display(message))       # [[BR]]/[[SP]] 占位符还原后只算 1 字节

    流程：确定性压缩词典 → （仍超长的）交给模型压缩 → 最终按字符边界裁尾。
    所有改动都经过 hanzi2kanji 字位映射与 CP932 校验。

    python repair_length.py <jp.json> --meta <meta.json> --zh <zh.json> \
        [--rounds 2] [--batch 40] [--no-llm]
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from translate import (HanziMap, normalise_markers, fix_punct, cp932_clean,
                       translate_batch)
from gsx_text import from_display
from names_map import build_name_pairs, apply_names

SHORTEN_SYS = ('你是中文编辑。下面每一条给出一句日文原文、以及它偏长的中文译文，'
               '要求在完全保留原意的前提下把译文压缩到指定字符数以内'
               '（每个汉字/假名/标点算 1 个字符，[[BR]]、[[SP]] 各算 1 个字符）。'
               '输出等长 JSON 数组，只输出压缩后的中文，不要解释、不要编号。'
               '优先精简虚词与重复修饰，保留专有名词。')

# 确定性压缩词典（仅对超预算条目使用）
DETERM = [
    ('什么的', '等'), ('的时候', '时'), ('的样子', '样'), ('是不是', '是'),
    ('已经', '已'), ('什么', '啥'), ('因为', '因'), ('所以', '故'),
    ('但是', '但'), ('而且', '且'), ('非常', '很'), ('这个', '这'),
    ('那个', '那'), ('一样', '同'), ('没有', '没'), ('知道', '晓'),
    ('如果', '若'), ('还是', '仍'), ('只是', '仅'), ('自己', '己'),
    ('可以', '可'), ('我们', '咱'), ('你们', '你'), ('他们', '他'),
    ('不是', '非'), ('就是', '即'), ('然后', '再'), ('之后', '后'),
    ('现在', '现'), ('一些', '些'), ('感觉', '觉'), ('认为', '认'),
    ('真的', '真'), ('全部', '全'), ('所有', '全'), ('开始', '始'),
]


def enc_len(s):
    try:
        return len(from_display(s))
    except Exception:
        return 10 ** 9


def budget_bytes(m):
    return m['slot'] - 1 - len(m['prefix']) - len(m['suffix'])


def deterministic_shorten(s, lim_bytes):
    if enc_len(s) <= lim_bytes:
        return s
    for a, b in DETERM:
        while a in s and enc_len(s) > lim_bytes:
            s = s.replace(a, b, 1)
    for mark in ('\u3002', '\uff01', '\uff1f', '\u3001', '\uff0c', '\u2026'):
        if enc_len(s) > lim_bytes and s.endswith(mark):
            s = s[:-1]
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jp_json')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--zh', required=True)
    ap.add_argument('--rounds', type=int, default=2)
    ap.add_argument('--batch', type=int, default=40)
    ap.add_argument('--no-llm', action='store_true')
    ap.add_argument('--cache', default=None)
    a = ap.parse_args()

    hm = HanziMap(os.path.join(ROOT, 'hanzi2kanji_table.txt'),
                  os.path.join(HERE, 'jis_supplement.txt'))
    nm = build_name_pairs(hm)
    zh = json.load(open(a.zh, encoding='utf-8'))
    meta = {m['id']: m for m in json.load(open(a.meta, encoding='utf-8'))['entries']}
    for e in zh:
        e.setdefault('pre_jp', '')

    def over():
        return [e for e in zh if enc_len(e['message']) > budget_bytes(meta[e['id']])]

    print('[repair] initially over budget: %d / %d' % (len(over()), len(zh)), flush=True)

    for rnd in range(0 if a.no_llm else a.rounds):
        todo, det = [], 0
        for e in zh:
            lim = budget_bytes(meta[e['id']])
            if enc_len(e['message']) > lim:
                s2 = deterministic_shorten(e['message'], lim)
                if s2 != e['message']:
                    e['message'] = s2
                    det += 1
                if enc_len(e['message']) > lim:
                    todo.append((e, lim))
        print('[repair] round %d : deterministic fixed %d, still over %d'
              % (rnd + 1, det, len(todo)), flush=True)
        if not todo:
            break
        batches = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
        fixed = 0
        for bi, b in enumerate(batches):
            lines = []
            for e, lim in b:
                plain = e['message'].replace('[[BR]]', '\u23ce').replace('[[SP]]', '\u00b6')
                marks = plain.count('\u23ce') + plain.count('\u00b6')
                n_chars = max(1, (lim - marks) // 2 + marks)
                lines.append('原文：%s\n超长译文（压缩到不超过 %d 个字符）：%s'
                             % (e['pre_jp'].replace('[[BR]]', '\u23ce')
                                .replace('[[SP]]', '\u00b6'), n_chars, plain))
            res = translate_batch(lines, system=SHORTEN_SYS)
            if res is None:
                print('   batch %d failed' % bi, flush=True)
                continue
            for (e, lim), t in zip(b, res):
                t = t.replace('\u23ce', '[[BR]]').replace('\u00b6', '[[SP]]')
                t = normalise_markers(t)
                t = hm.apply(t)
                t = apply_names(t, nm)
                t = fix_punct(t)
                t, _ = cp932_clean(t)
                if t and enc_len(t) < enc_len(e['message']):
                    e['message'] = t
                    fixed += 1
        print('   llm shortened %d entries' % fixed, flush=True)
        if fixed == 0:
            break

    trimmed = 0
    for e in zh:
        lim = budget_bytes(meta[e['id']])
        if enc_len(e['message']) > lim:
            t = e['message']
            while t and enc_len(t) > lim:
                t = t[:-1]
            if t:
                e['message'] = t
                trimmed += 1
    # Keep cache.json in sync — but store the **pre-image** of the final text
    # (the character the mapping was applied to), not the mapped text itself:
    #   cache value  --HanziMap-->  shipped text
    # so a later `finalize.py` re-run reproduces the shipped bytes exactly and
    # never double-applies the slot table (see docs/engine_report.md 5.2).
    cpath = a.cache or os.path.join(os.path.dirname(os.path.abspath(a.zh)), 'cache.json')
    cache = json.load(open(cpath, encoding='utf-8')) if os.path.exists(cpath) else {}
    for e in zh:
        msg = e['message'] or e['pre_jp']
        cache[e['pre_jp']] = ''.join(hm.shown_of(c) for c in msg)
    json.dump(cache, open(cpath, 'w', encoding='utf-8'), ensure_ascii=False)
    json.dump(zh, open(a.zh, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[repair] trimmed %d entries; remaining over budget: %d'
          % (trimmed, len(over())))


if __name__ == '__main__':
    main()
