#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opcodelist.py —— 「Hello,world.」(NitroPlus / OC 引擎) 脚本指令集定义
================================================================================
本文件是 nps_disasm.py / nps_asm.py / nps_text.py 的唯一真值源。

分析来源（证据链）
------------------
1. system.dll 内嵌的指令 / 键名字符串表（地址即证据）:
     0xC43A8 IMG    0xC43AC MOVIE   0xC43B4 VOICE  0xC43C0 BGM    0xC43C4 SOUND
     0xC43CC SYSINFO 0xC43D4 CELL   0xC43DC DISC   0xC43E4 LOAD   0xC43EC NAMEENTRY
     0xC43F8 SYSTEM 0xC4400 END     0xC4404 CALC   0xC4410 HIDEMESSAGE
     0xC4440 /FONT  0xC4448 FONT    0xC4450 BOX    0xC4454 SELECT 0xC445C RETURN
     0xC4464 CALL   0xC446C MARKER  0xC4474 CHOICE 0xC4480 WAIT   0xC448C ASYNCHRONOUS
     0xC449C BGANIM 0xC44A4 BGMOVIE 0xC44AC FLASH  0xC44B4 FACE   0xC44BC QUAKE
     0xC44C4 CLEAR  0xC44CC SCROLL  0xC44D4 ANIM   0xC44DC WIPE   0xC44E4 LAYER
     0xC44EC GLASS  0xC44F4 BUSTUP  0xC44FC BACKGROUND
     0xC59AC MACRO  0xC59B4 DEFINE  0xC59C0 INCLUDE （预处理指令）
     0xC4D74 "NitroPlus"  0xC578C "Software\nitroplus\"  0xC57A0 "game.ini"
     0xC4EB0 OCWindow 0xC508C OCGameApplicationWindow  0xC231A0 OCArchive 相关 RTTI
2. 语料统计（unpack/script 下 420 个 .nps/.h 文件，见 work/tag_schema.json）
3. 配置证据：unpack/system/system.ini（fontName/禁则表）、textbox.ini（BOX0..N 版式）、
   choice.ini、backlog.ini、align.ini、cgmode.ini、game.ini（Title="Hello,world."）

脚本语法概览
------------
.nps = CP932 纯文本，逐行解释；行类型四类：
    · 注释行     以 // 开头（引擎忽略）
    · 命令行     形如 <TAG ATTR="V" ...>，可一行多命令
    · 文本行     直接显示的文字（可内嵌 <K> 等行内记号）
    · 空行       无意义，仅排版
字符串常量：双引号内为 CP932 字节；引号可省略（语料中大量 <BACKGROUND SRC=bg0064> 形式）。
换行：全部 CRLF。文件末尾可有/无可选换行。
"""

# =============================================================================
# 一、行内记号（出现在文本行内部，参与显示或排版）
# =============================================================================
INLINE = {
    'K':          dict(display=False, desc='消息内换行（强制断行，屏幕上换一行）'),
    'k':          dict(display=False, desc='同 <K>（小写写法）'),
    'HR':         dict(display=False, desc='水平线（HTML 残留，运行时无显示效果）'),
    'BIG':        dict(display=True,  desc='大字号（HTML 残留；用于 WORKNAME/标题行）'),
    'B':          dict(display=True,  desc='粗体（HTML 残留）'),
    'R':          dict(display=True,  desc='注音/ルビ：<R TEXT="读音">正文</R>，TEXT 属性为显示的小字'),
    'wait':       dict(display=False, desc='行内等待：<wait time="n">，逐字显示节奏控制'),
    'clear':      dict(display=False, desc='清除消息框'),
    'CLEAR':      dict(display=False, desc='清除消息框（大写写法）'),
    'CLAER':      dict(display=False, desc='【错别字】语料中出现 14 次的 CLEAR 误写；引擎侧应当被忽略'),
    'SELECT':     dict(display=False, desc='选择肢区域的开始/结束标记'),
    'Hidemessage':dict(display=False, desc='隐藏消息框'),
    'FLASH':      dict(display=False, desc='画面闪白/闪黑'),
    'BGM':        dict(display=False, desc='播放 BGM（行内写法）'),
    'LOAD':       dict(display=False, desc='读取存档界面'),
    'PRE':        dict(display=False, desc='HTML 残留'),
    'HEAD':       dict(display=False, desc='HTML 残留'),
    'TITLE':      dict(display=False, desc='HTML 残留（内容为 // 注释，不显示）'),
    'HTML':       dict(display=False, desc='HTML 残留'),
}

# =============================================================================
# 二、指令（TAG）表 —— 全部来自 system.dll 字符串表 + 语料实测
#     kind:  show=演出  flow=流程  text=文本相关  pre=预处理  html=HTML 残留  meta=系统
#     attrs: 属性名 -> (是否必填, 取值域/示例, 是否为“显示文本”)
# =============================================================================
OPCODES = {
    # ---- 演出类 -------------------------------------------------------------
    'BACKGROUND': dict(kind='show', mnemonic='BACKGROUND', desc='切换背景图', attrs={
        'SRC': (True, '背景资源名（cg/ 或 data/ 下），如 bg0064 / gate05 / white', False),
        'SHADE': (False, '0..4 明暗档', False), 'shade': (False, '同 SHADE', False),
        'X': (False, '像素', False), 'Y': (False, '像素', False),
        'ZOOM': (False, '百分比', False), 'zoom': (False, '百分比', False),
        'zoomtype': (False, '缩放方式', False), 'TRANSPARENT': (False, '0..100 透明度', False),
        'REVERSE': (False, 'on 左右翻转', False), 'background': (False, '子参数', False)}),
    'WIPE': dict(kind='show', mnemonic='WIPE', desc='转场特效（淡入淡出/移动/百叶窗等）', attrs={
        'EFFECT': (True, 'fade/light/lcenter/left/right/blackout/whiteout/tiny/blind/slash/'
                        'slash2/noize/raster/spiral/transform/under/over/delayfade/…', False),
        'TIME': (True, '帧数', False), 'time': (False, '帧数', False),
        'shade': (False, '明暗', False)}),
    'LAYER': dict(kind='show', mnemonic='LAYER', desc='在指定图层号上叠放前景图（含主人公窗等）', attrs={
        'SRC': (True, '资源名', False), 'NUM': (True, '2..4 图层号', False),
        'TRANSPARENT': (False, '0..100', False), 'X': (False, '像素', False), 'Y': (False, '像素', False),
        'ZOOM': (False, '百分比', False), 'ZOOMTYPE': (False, '缩放方式', False),
        'SHADE': (False, '明暗', False), 'shade': (False, '明暗', False),
        'MODE': (False, 'on/off', False), 'REVERSE': (False, 'on', False), 'align': (False, '对齐', False)}),
    'BUSTUP': dict(kind='show', mnemonic='BUSTUP', desc='半身立绘：设置角色身体/表情并显示', attrs={
        'NAME': (True, '角色名（同时决定名牌显示）', False),
        'BODY': (True, '服装/姿势资源名', False), 'FACE': (False, '表情名', False),
        'face': (False, '表情名', False), 'MODE': (False, 'on/off', False),
        'ALIGN': (False, 'center/left/right/kwcenter/…（align.ini 定义）', False),
        'align': (False, '同 ALIGN', False), 'X': (False, '像素', False), 'Y': (False, '像素', False),
        'zoom': (False, '百分比', False)}),
    'FACE': dict(kind='show', mnemonic='FACE', desc='表情差分切换（FACE.INI 定义）', attrs={
        'NAME': (False, '角色名', False), 'SRC': (False, '表情名', False)}),
    'SCROLL': dict(kind='show', mnemonic='SCROLL', desc='图层滚动', attrs={
        'NUM': (True, '图层号', False), 'X': (False, '位移，@ 前缀=相对', False),
        'Y': (False, '位移', False), 'TIME': (False, '帧数', False), 'COUNT': (False, '帧数', False),
        'count': (False, '帧数', False), 'num': (False, '图层号', False), 'x': (False, '位移', False),
        'y': (False, '位移', False), 'time': (False, '帧数', False), 'delay': (False, 'TRUE', False),
        'silent': (False, 'TRUE', False), 'num2': (False, '第二层', False),
        'x2': (False, '第二层位移', False), 'y2': (False, '第二层位移', False),
        'count2': (False, '第二段帧数', False), 'SRC': (False, '音效名', False)}),
    'FLASH': dict(kind='show', mnemonic='FLASH', desc='闪光特效', attrs={
        'SRC': (False, '颜色/图片', False), 'COUNT': (False, '次数', False),
        'BEFORE': (False, '进入特效', False), 'AFTER': (False, '退出特效', False),
        'TIME': (False, '帧数', False), 'WAIT': (False, '0/1', False)}),
    'QUAKE': dict(kind='show', mnemonic='QUAKE', desc='画面震动', attrs={
        'X': (False, '振幅', False), 'Y': (False, '振幅', False), 'COUNT': (False, '次数', False)}),
    'BGANIM': dict(kind='show', mnemonic='BGANIM', desc='背景动画（语料未使用）', attrs={}),
    'BGMOVIE': dict(kind='show', mnemonic='BGMOVIE', desc='背景视频（语料未使用）', attrs={}),
    'ANIM': dict(kind='show', mnemonic='ANIM', desc='精灵动画（语料未使用）', attrs={}),
    'GLASS': dict(kind='show', mnemonic='GLASS', desc='玻璃特效（语料未使用）', attrs={}),
    'CELL': dict(kind='show', mnemonic='CELL', desc='单元动画（语料未使用）', attrs={}),
    'IMG': dict(kind='show', mnemonic='IMG', desc='显示图片到指定 ID 的“事件显示”槽位', attrs={
        'ID': (True, '槽位名', False), 'src': (True, '资源名', False)}),
    'MOVIE': dict(kind='show', mnemonic='MOVIE', desc='播放 MPEG（mpeg.pak 内）', attrs={
        'SRC': (True, 'mpeg\\xxx.mpg', False)}),
    # ---- 声音 ---------------------------------------------------------------
    'SE': dict(kind='show', mnemonic='SE', desc='播放音效', attrs={
        'ID': (True, '声道号 1..4', False), 'id': (False, '声道号', False),
        'SRC': (True, '音效名（system.ini/音量表映射到 sound.pak）', False),
        'src': (False, '音效名', False), 'MODE': (False, 'normal/fadein/fadeout', False),
        'mode': (False, 'normal/fadein/fadeout', False), 'LOOP': (False, 'on/off', False),
        'loop': (False, 'on/off', False), 'TIME': (False, '渐入渐出帧数', False),
        'time': (False, '帧数', False), 'VOL': (False, '音量', False), 'vol': (False, '音量', False),
        'EFFECT': (False, '音效滤镜，如 エコー', False), 'effect': (False, '音效滤镜', False)}),
    'BGM': dict(kind='show', mnemonic='BGM', desc='播放 BGM', attrs={
        'SRC': (True, 'BGM 名（sound.pak 内）', False), 'src': (False, 'BGM 名', False),
        'MODE': (False, 'fadein/fadeout/normal', False), 'mode': (False, '同上', False),
        'old': (False, '旧曲处理 fadeout', False), 'TIME': (False, '帧数', False),
        'time': (False, '帧数', False), 'loop': (False, 'on/off', False),
        'vol': (False, '音量', False)}),
    'VOICE': dict(kind='text', mnemonic='VOICE', desc='播放角色语音，并【设置名牌显示名】', attrs={
        'NAME': (True, '角色名 —— 屏幕上名牌显示的字符串', False),
        'CLASS': (False, '音声类别（game.ini [音声] 分组，用于音量开关）', False),
        'SRC': (True, '语音文件（voice.pak 内，如 01_1\\0000100）', False)}),
    # ---- 流程 ---------------------------------------------------------------
    'MARKER': dict(kind='flow', mnemonic='MARKER', desc='定义跳转标签（符号，供 <A HREF="#标签"> 使用）', attrs={
        'NAME': (True, '标签名，以 # 开头，如 #OP / #共通１', False),
        'name': (False, '同 NAME', False)}),
    'A': dict(kind='flow', mnemonic='A', desc='跳转/选择肢锚点：HREF=脚本文件 或 #标签；带 TEXT= 即为选项文字',
              attrs={
        'HREF': (False, '目标脚本文件（如 01_110.nps）或 #标签', False),
        'ID': (False, '锚点标记（CHOICE 等）', False),
        'TEXT': (False, '★显示文本（选项文字）', True),
        'OPERATOR': (False, '条件赋值（如 千絵梨画材購入=true）', False)}),
    'CHOICE': dict(kind='flow', mnemonic='CHOICE', desc='选择肢条目', attrs={
        'TEXT': (True, '★显示文本（选项文字）', True),
        'HREF': (True, '目标 #标签', False), 'FILE': (False, '缩略图资源', False),
        'TYPE': (False, '2/3 选项框样式', False), 'OPERATOR': (False, '条件', False),
        'ONSE': (False, '音效名', False), 'PUSHSE': (False, '音效名', False),
        'disable': (False, '禁用条件', False), 'DI': (False, '预留', False)}),
    'SELECT': dict(kind='flow', mnemonic='SELECT', desc='选择肢区域开始'),
    'RETURN': dict(kind='flow', mnemonic='RETURN', desc='脚本返回（子过程/菜单）'),
    'CALL': dict(kind='flow', mnemonic='CALL', desc='调用子脚本'),
    'ASYNC': dict(kind='flow', mnemonic='ASYNCHRONOUS', desc='异步执行（语料未使用）', attrs={}),
    'END': dict(kind='flow', mnemonic='END', desc='结束游戏（回到标题）', attrs={
        'OPTION': (False, 'CLOSE', False)}),
    'LOAD': dict(kind='flow', mnemonic='LOAD', desc='显示读档/存档界面', attrs={
        'NO': (False, 'new 或槽位号', False), 'MODE': (False, '保存/读取模式', False)}),
    'SYSTEM': dict(kind='meta', mnemonic='SYSTEM', desc='系统指令（计时器/菜单/结局/URL/配置等）', attrs={
        'CMD': (True, 'timerclear / sceneChange / skipStop / cgmode / ending / url / config / menu', False),
        'INIT': (False, 'TRUE', False), 'MENU': (False, '菜单页号 #00/#A2/#B2', False),
        'FILE': (False, '结局资源路径 ; 分隔', False), 'LOGO': (False, 'Logo 视频', False),
        'END': (False, '结局脚本路径 ; 分隔', False), 'URL': (False, '网址', False),
        'SKIP': (False, '跳过开关', False), 'ROLL': (False, '滚动设置', False)}),
    'CALC': dict(kind='meta', mnemonic='CALC', desc='全局变量赋值（WORKNAME ← OPERATOR）', attrs={
        'WORKNAME': (True, '变量名（01_100 / date / クリア / ヒロイン …）', False),
        'workname': (False, '同 WORKNAME', False),
        'OPERATOR': (True, '变量值；★可能被后续文本显示（日期、旗帜名等）', True),
        'operator': (False, '同 OPERATOR', True), 'mode': (False, 'global', False)}),
    'SYSINFO': dict(kind='meta', mnemonic='SYSINFO', desc='查询系统信息写入变量', attrs={
        'workname': (True, '目标变量名', False), 'infoname': (True, '被查询项，如 HasContinue?', False)}),
    'DISC': dict(kind='meta', mnemonic='DISC', desc='光盘媒体检查', attrs={
        'NAME': (True, 'Game 等', False)}),
    'WAIT': dict(kind='flow', mnemonic='WAIT', desc='等待（帧）', attrs={
        'TIME': (True, '帧数', False), 'SYNC': (False, 'TRUE 等待显示同步', False),
        'wait': (False, '帧数', False)}),
    # ---- 文本/版式 ----------------------------------------------------------
    'BOX': dict(kind='text', mnemonic='BOX', desc='切换消息框样式（textbox.ini 的 [BOXn]）', attrs={
        'TYPE': (True, '框编号 0..N', False), 'type': (False, '框编号', False),
        'name': (False, '★显式设置名牌显示名（极少用，仅 17_210.nps 一处）', True)}),
    'CLEAR': dict(kind='text', mnemonic='CLEAR', desc='清除消息框内容'),
    'HIDEMESSAGE': dict(kind='text', mnemonic='HIDEMESSAGE', desc='隐藏消息框', attrs={}),
    'FONT': dict(kind='text', mnemonic='FONT', desc='字体设置（语料未使用，配置在 system.ini）', attrs={}),
    'NAMEENTRY': dict(kind='text', mnemonic='NAMEENTRY', desc='角色名登录（语料未使用）', attrs={}),
    'COMMENT': dict(kind='pre', mnemonic='//', desc='注释（非指令，见注释语法）'),
    # ---- 预处理 -------------------------------------------------------------
    'INCLUDE': dict(kind='pre', mnemonic='INCLUDE', desc='包含定义文件', attrs={
        'SRC': (True, 'chara.h / include.h', False)}),
    'DEFINE': dict(kind='pre', mnemonic='DEFINE', desc='文本替换宏定义：SRC → DEST', attrs={
        'SRC': (True, '被替换串（如 <コマンド 通常>）', False),
        'DEST': (True, '替换结果（如 <SYSTEM MENU="#00">）', False),
        'ID': (False, '标记名', False)}),
    'MACRO': dict(kind='pre', mnemonic='MACRO', desc='命令宏定义（<MACRO NAME="X">…</MACRO>）', attrs={
        'NAME': (True, '宏名', False)}),
    'SCC': dict(kind='pre', mnemonic='vssver', desc='Visual SourceSafe 版本文件，非脚本'),
    # ---- HTML 残留（引擎解析时忽略，但保留在文件中） -------------------------
    'HTML': dict(kind='html', mnemonic='HTML', desc='HTML 残留：脚本开始标记'),
    'HEAD': dict(kind='html', mnemonic='HEAD', desc='HTML 残留'),
    'TITLE': dict(kind='html', mnemonic='TITLE', desc='HTML 残留：内容为场景 ID 注释'),
    'BODY': dict(kind='html', mnemonic='BODY', desc='HTML 残留'),
    'PRE': dict(kind='html', mnemonic='PRE', desc='HTML 残留：正文区开始'),
    'HR': dict(kind='html', mnemonic='HR', desc='HTML 残留：水平线'),
    'META': dict(kind='html', mnemonic='meta', desc='HTML 残留：声明 charset=x-sjis'),
    'BIG': dict(kind='html', mnemonic='BIG', desc='HTML 残留：大字号'),
    'B': dict(kind='html', mnemonic='B', desc='HTML 残留：粗体'),
    'R': dict(kind='text', mnemonic='R', desc='注音（ルビ）', attrs={
        'TEXT': (True, '★注音文字（显示在正文上方的小字）', True),
        'text': (False, '同 TEXT', True)}),
    'K': dict(kind='text', mnemonic='K', desc='消息内换行'),
}

# =============================================================================
# 三、名牌（说话人）来源
# =============================================================================
NAME_SOURCE_TAGS = ('VOICE', 'voice', 'BUSTUP', 'BOX', 'box', 'FACE')
NAME_ATTRS = ('NAME', 'name')

# 含“显示文本”的属性（提取用）
DISPLAY_ATTRS = {t: [a for a, v in spec.get('attrs', {}).items() if v[2]]
                 for t, spec in OPCODES.items()}

# 文本行行内记号（显示相关的白名单）
INLINE_DISPLAY_OK = {'K', 'k', 'BIG', 'B', 'R', 'wait', 'clear', 'CLEAR', 'HR', 'SELECT',
                     'Hidemessage', 'CLAER', 'PRE', 'HEAD', 'TITLE', 'HTML', 'BGM', 'FLASH', 'LOAD'}

# =============================================================================
# 四、转义与占位符（asm.txt 规范，见 CLAUDE.md 2.3）
# =============================================================================
ESCAPES = {
    '"': '\\"', '\\': '\\\\', '\r': '{{0D}}', '\n': '{{0A}}', '\t': '{{09}}',
}

def esc(s: str) -> str:
    out = []
    for ch in s:
        if ch in ESCAPES:
            out.append(ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append('{{%02X}}' % ord(ch))
        else:
            out.append(ch)
    return ''.join(out)


def unesc(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == '"':
                out.append('"'); i += 2; continue
            if nxt == '\\':
                out.append('\\'); i += 2; continue
        if s.startswith('{{', i):
            j = s.find('}}', i)
            if j > 0:
                hexs = s[i + 2:j]
                if 2 <= len(hexs) <= 4:
                    out.append(chr(int(hexs, 16))); i = j + 2; continue
        out.append(s[i]); i += 1
    return ''.join(out)


if __name__ == '__main__':
    print(f'指令 {len(OPCODES)} 条 / 行内记号 {len(INLINE)} 条')
    for t, v in OPCODES.items():
        req = [a for a, x in v.get('attrs', {}).items() if x[0]]
        dis = DISPLAY_ATTRS.get(t) or []
        print(f'  {t:14s} [{v["kind"]:4s}] 必填={",".join(req) or "-":28s} 显示属性={",".join(dis) or "-"}')
