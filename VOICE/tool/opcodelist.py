"""LOVELESS / HAM 引擎脚本 VM 指令集定义

来源：Game.exe 静态逆向（Ghidra MCP + capstone）
  · 主解释器            FUN_00410530   (0x410530)
  · 主分发跳转表        0x410F44       (19 项)
  · 0x0B 算术 VM 处理器  FUN_004150B0   (0x4150B0)
  · 0x0B 子操作码跳转表  0x419950       (0x55 = 85 项)
  · 操作数读取器：
      FUN_00414EE0  读 4 字节小端 = 变量槽引用（必须形如 0b10xxxxxx，否则报
                    「フラグエラー / フラグを指定するところに定数が指定されています」）
      FUN_00414E30  读 4 字节小端 = 取值（高 2 位 0b10 → 变量槽下标，否则为常量）
      FUN_00414DE0  读 2 字节小端 = 有符号 16 位立即数

语义约定
--------
· 脚本被整体读入内存，游标 param_2 线性前进。
· 每个字节先用两张分类表判定「操作码 or 文本」：
      ((&DAT_00468EA1)[b] & 0x0D) == 0  &&
      (*(u16*)(&DAT_00454D5E + b*2) & 0x107) == 0  &&  b != ' '
  成立则 b 是操作码，否则该字节累加进消息缓冲（= 显示的文本）。
  DAT_00454D5E 就是 MSVCRT 的 _ctype 表（UPPER=1 LOWER=2 DIGIT=4 PUNCT=0x10 CTRL=0x20…），
  于是 ASCII 字母/数字天然是"文本"。DAT_00468EA1 位于 .data 的 BSS 段（文件里没有），
  由运行期 CRT 初始化。
· 文本区里 0x0A 是「换行」操作码，把一条消息切成多个物理行；
  0x01 是「消息结束 / 等待按键」。
· 存在大量**绝对 16 位脚本内偏移**指令（0x0B 子码 0x32..0x39），
  所以变长改写文本会打断控制流 → 本项目采用定长就地替换。

本模块只描述指令集，不含执行逻辑；反汇编/汇编见 disassembler.py / assembler.py。
"""

IMAGE_BASE = 0x400000

# ── 0x0B 的子操作码跳转表（subop = 索引 + 1）──────────────────────────────
SUBOP_TABLE_ADDR = 0x419950
SUBOP_COUNT = 0x55

READER = {'flag': ('FUN_00414EE0', 4), 'val': ('FUN_00414E30', 4), 's16': ('FUN_00414DE0', 2)}

# 每个子操作码的操作数布局（依处理器内读取器调用顺序自动提取 + 人工核对）
#   flag4 = 4 字节变量槽引用   val4 = 4 字节取值   s16 = 2 字节立即数   rawN = N 字节原样
SUBOP_OPS = {
    0x01: ['flag4', 'val4'],
    0x02: ['flag4', 'flag4'],
    0x03: ['flag4', 'flag4'],
    0x04: ['flag4', 'flag4'],
    0x05: ['flag4', 'val4'],
    0x06: ['flag4', 'val4', 'val4'],
    0x07: ['flag4', 'flag4', 'val4'],
    0x08: ['flag4', 'flag4', 'flag4'],
    0x09: ['flag4', 'flag4', 'flag4'],
    0x0A: ['flag4', 'flag4'],
    0x0B: ['flag4', 'val4'],
    0x0C: ['flag4', 'flag4'],
    0x0D: [], 0x0E: [],                       # 未实现 → default
    0x0F: ['flag4', 'flag4', 'flag4'],
    0x10: ['flag4', 'flag4', 'flag4'],
    0x11: ['flag4', 'flag4'],
    0x12: ['flag4', 'flag4'],
    0x13: ['flag4', 'flag4'],
    0x14: ['flag4', 'flag4'],
    0x15: ['flag4'],
    0x16: ['flag4', 'flag4'],
    0x17: ['flag4', 'flag4'],
    0x18: ['flag4', 'flag4'],
    0x19: ['flag4', 'flag4'],
    0x1A: ['flag4', 'flag4'],
    0x1B: ['flag4', 'flag4'],
    0x1C: ['flag4', 'flag4'],
    0x1D: ['val4', 'flag4', 'flag4'],
    0x1E: [], 0x1F: [], 0x20: [], 0x21: [],
    0x22: ['flag4', 'flag4'],
    0x23: ['flag4', 'flag4'],
    0x24: ['flag4', 'val4'],
    0x25: ['raw6'],
    0x26: [],                                  # 向后扫描 0b25 标记（循环）
    0x27: ['raw6'],
    0x28: ['val4'],
    0x29: ['raw6'],
    0x2A: ['raw6'],
    0x2B: ['raw6'],
    0x2C: ['raw6', 'val4'],
    0x2D: ['val4', 'val4'],
    0x2E: ['val4'],
    0x2F: ['raw6'],
    0x30: ['raw6'],
    0x31: ['val4'],
    # 0x32..0x39：与「绝对 16 位脚本偏移」有关的条件/无条件跳转
    0x32: ['flag4', 's16'],                    # if (var[flag] >  0) goto T
    0x33: ['flag4', 's16'],                    # if (var[flag] == 0) goto T
    0x34: ['flag4', 's16'],
    0x35: ['flag4', 's16'],
    0x36: ['s16'],
    0x37: ['flag4', 's16'],
    0x38: ['s16'],                             # goto T（状态 4：跳转到绝对偏移 T）
    0x39: ['flag4', 's16', 's16'],
    0x3A: [], 0x3B: [], 0x3C: [], 0x3D: [], 0x3E: [], 0x3F: [],
    0x40: [], 0x41: [], 0x42: [], 0x43: [], 0x44: [], 0x45: [],
    0x46: ['val4', 'val4', 'val4', 's16', 'val4', 's16'],
    0x47: ['val4', 'val4', 'val4', 'val4', 'val4', 's16', 'val4', 'val4', 'val4', 'val4', 's16'],
    0x48: [], 0x49: [],
    0x4A: ['s16'],
    0x4B: [],
    0x4C: ['s16', 's16'] + ['val4'] * 10,
    0x4D: ['s16', 'val4', 's16'] + ['val4'] * 10,
    0x4E: [],
    0x4F: ['s16', 'val4', 's16'] + ['val4'] * 10,
    0x50: ['s16', 's16'],
    0x51: [],
    0x52: ['s16', 'val4', 's16'] + ['val4'] * 8,
    0x53: ['s16'] + ['val4'] * 8 + ['s16'] + ['val4'] * 4,
    0x54: ['flag4', 'val4'],
    0x55: ['flag4', 'flag4'] + ['val4'] * 7,
}

# ── 顶层操作码（FUN_00410530 的 switch）────────────────────────────────────
# kind: 'none'=无操作数  'imm1'=1 字节  'cstr'=NUL 结尾字符串  'sub'=子分发器
TOP_OPS = {
    0x00: ('END',        'none',  '脚本结束（主循环首个判定）'),
    0x01: ('MSGWAIT',    'none',  '消息结束 / 等待按键，并把续跑偏移写入 state'),
    0x03: ('SCENEGOTO',  'none',  '场景跳转（状态 3）'),
    0x04: ('SCENELOAD',  'cstr',  '切换脚本文件（读 NUL 结尾文件名，状态 5/4）'),
    0x05: ('SCENECALL',  'cstr',  '同 0x04，记录返回偏移'),
    0x06: ('SCENE',      'sub',   '场景命令子分发器 FUN_0041BCC0（跳转表 0x41C8F0，22 项）'),
    0x08: ('FLUSH',      'none',  '刷新消息缓冲'),
    0x09: ('WAIT',       'imm1',  '等待 imm*100ms（PeekMessage 泵消息）'),
    0x0A: ('NEWLINE',    'none',  '换行（推进物理行；同时把文本切成两段）'),
    0x0B: ('EXPR',       'sub',   '算术 / 变量 VM，FUN_004150B0，85 个子操作码'),
    0x0D: ('CMD0D',      'sub',   'FUN_0041C950（跳转表 0x41D492，15 项）'),
    0x0E: ('SHAKE',      'imm1',  '画面抖动 imm 次'),
    0x0F: ('FADE',       'imm1',  '效果切换（FUN_00408C40）'),
    0x11: ('SYSTEM',     'sub',   '系统菜单子分发器 FUN_00411470（跳转表 0x414D3F，14 项）'),
    0x13: ('GFX',        'sub',   '绘图子分发器 FUN_00419DA0（跳转表 0x41A7AB，24 项）'),
    0x15: ('DRAW',       'sub',   '绘制子分发器 FUN_00419B40（跳转表 0x41B054，25 项）'),
    0x16: ('CMD16',      'sub',   'FUN_0041D500（跳转表 0x41E100，7 项）'),
    0x19: ('BREAK',      'none',  '中断当前消息'),
    0x23: ('TAG#',       'raw45', '# + 4/5 字节内联标记（见 FUN_00410530 case '#'）'),
    0x24: ('TAG$',       'raw45', '$ + 5/6 字节内联标记'),
    0x25: ('TAG%',       'raw34', '% + 3/4 字节内联标记'),
}

# 已知的顶层跳转表（用于复查）
JUMP_TABLES = {
    0x410F44: ('FUN_00410530 top-level', 19),
    0x4113E5: ('FUN_00410FC0 status dispatch', 7),
    0x414D3F: ('FUN_00411470 (op 0x11)', 14),
    0x419950: ('FUN_004150B0 (op 0x0B)', 85),
    0x41A7AB: ('FUN_00419DA0 (op 0x13)', 24),
    0x41B054: ('FUN_00419B40 (op 0x15)', 25),
    0x41C8F0: ('FUN_0041BCC0 (op 0x06)', 22),
    0x41D492: ('FUN_0041C950 (op 0x0D)', 15),
    0x41E100: ('FUN_0041D500 (op 0x16)', 7),
}

# 脚本文件名 → 归档：由 Game.ini 的 FILEH/FILED 指向 data.hed/data.dat
ARCHIVE = {
    'header': 'DATA.HED',
    'data': 'DATA.DAT',
    'xor_keys': [b'RIC', b'VOICE', b'SIDE-B'],
    'header_layout': '15B data.dat 名 + u16 count + (count+1)*20B 条目(name[12],A,offset)',
    'note': '条目按 offset 升序，size = 下一条 offset - 本条 offset；末条为 end 哨兵',
}
