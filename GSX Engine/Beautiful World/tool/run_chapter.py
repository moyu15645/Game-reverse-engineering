#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_chapter.py — 一章端到端汉化流水线

    python tool/run_chapter.py <1|2|3> [--skip-translate] [--skip-asm]

步骤
  1. 解包 filepack.bin            -> work/ch<N>/unpack/
  2. 提取文本 + 角色名            -> work/ch<N>/text/jp.json  (+ meta, names)
  3. 批量机翻（缓存续跑）          -> work/ch<N>/text/cache.json
  4. 译文后处理（字位映射/校验）    -> work/ch<N>/text/zh.json
  5. 注入并重新封包               -> work/ch<N>/release/filepack.bin
  6. 复制引擎其余文件             -> release/<游戏目录>/
  7. 反汇编 + 汇编 + 零突变校验    -> work/ch<N>/asm/, rebuild_asm/
"""
import os, sys, json, time, shutil, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
PY = sys.executable

CH = {
    '1': ('ch1', 'Beautiful World 1.1'),
    '2': ('ch2', 'Beautiful World 1.2'),
    '3': ('ch3', 'Beautiful World 1.3'),
}


def run(cmd):
    print('$ ' + ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        raise SystemExit('command failed: %s' % ' '.join(cmd))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('chapter', choices=['1', '2', '3'])
    ap.add_argument('--skip-translate', action='store_true')
    ap.add_argument('--skip-asm', action='store_true')
    ap.add_argument('--batch', default='120')
    ap.add_argument('--workers', default='10')
    a = ap.parse_args()

    ch, folder = CH[a.chapter]
    src = os.path.join(ROOT, folder)
    wd = os.path.join(ROOT, 'work', ch)
    os.makedirs(os.path.join(wd, 'text'), exist_ok=True)
    t0 = time.time()

    print('=== [1/7] 解包 %s' % folder, flush=True)
    run([PY, 'tool/fpack.py', 'unpack', os.path.join(src, 'filepack.bin'),
         os.path.join(wd, 'unpack')])

    print('=== [2/7] 提取文本', flush=True)
    run([PY, 'tool/gsx_text.py', 'extract', src, '-o', os.path.join(wd, 'text/jp.json'),
         '--meta', os.path.join(wd, 'text/jp.json.meta.json'), '--chapter', a.chapter])
    run([PY, 'tool/gsx_text.py', 'names', src, '-o', os.path.join(wd, 'text/names_jp.json')])

    if not a.skip_translate:
        print('=== [3/7] 机翻', flush=True)
        run([PY, '-u', 'tool/translate.py', os.path.join(wd, 'text/jp.json'),
             '-o', os.path.join(wd, 'text/zh.json'),
             '--cache', os.path.join(wd, 'text/cache.json'),
             '--batch', a.batch, '--workers', a.workers])

    print('=== [4/7] 回显修复 + 译文后处理 + 残留假名重译 + 长度修复', flush=True)
    run([PY, '-u', 'tool/retry_echo.py', os.path.join(wd, 'text/jp.json'),
         '--cache', os.path.join(wd, 'text/cache.json'), '--batch', '16', '--workers', '6'])
    run([PY, 'tool/finalize.py', os.path.join(wd, 'text/jp.json'),
         '--cache', os.path.join(wd, 'text/cache.json'),
         '-o', os.path.join(wd, 'text/zh.json')])
    run([PY, '-u', 'tool/retranslate.py', ch])
    run([PY, 'tool/finalize.py', os.path.join(wd, 'text/jp.json'),
         '--cache', os.path.join(wd, 'text/cache.json'),
         '-o', os.path.join(wd, 'text/zh.json')])
    run([PY, '-u', 'tool/repair_length.py', os.path.join(wd, 'text/jp.json'),
         '--meta', os.path.join(wd, 'text/jp.json.meta.json'),
         '--zh', os.path.join(wd, 'text/zh.json'), '--rounds', '3', '--batch', '25'])
    run([PY, 'tool/check_font_slots.py', '--chapter', folder])
    run([PY, 'tool/check_message_syntax.py'])

    print('=== [5/7] 注入 + 重新封包', flush=True)
    run([PY, 'tool/build_release.py', src,
         '--json', os.path.join(wd, 'text/zh.json'),
         '--meta', os.path.join(wd, 'text/jp.json.meta.json'),
         '--outdir', os.path.join(ROOT, 'release', folder)])

    if not a.skip_asm:
        print('=== [6/7] 反汇编 / 汇编', flush=True)
        run([PY, 'tool/gsx_disasm.py', src, '-o', os.path.join(wd, 'asm')])
        run([PY, 'tool/gsx_asm.py', os.path.join(wd, 'asm'), '-o', os.path.join(wd, 'rebuild_asm')])

    print('=== [7/7] 成品校验', flush=True)
    run([PY, 'tool/verify_release.py', os.path.join(ROOT, 'release', folder),
         '--meta', os.path.join(wd, 'text/jp.json.meta.json'),
         '--zh', os.path.join(wd, 'text/zh.json'), '--orig', folder])
    print('=== 完成，用时 %.0f 秒' % (time.time() - t0), flush=True)
    print('    发布目录: %s' % os.path.join(ROOT, 'release', folder), flush=True)


if __name__ == '__main__':
    main()
