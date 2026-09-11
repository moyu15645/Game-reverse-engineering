# 引擎分析报告 & 工具使用说明
## The Beautiful World（Enterbrain, 2011）— GSX 引擎汉化工程

---

## 1. 结论速览

| 项目 | 结论 |
| --- | --- |
| 引擎 | **GSX**（自研脚本 VM），宿主 `Beautiful World <章>.exe`（32 位 PE，无 DLL 分离） |
| 资源封包 | `filepack.bin`，**No-FilePlus**（`Dm-No-FilePlusD`）自研封包，两种形态（带名字表 / 纯索引） |
| 剧本 | `/Data/script/CoD.cpt`，**差分替换流密码** + 自研容器；内部分 106 / 201 / 202 个脚本块 |
| 脚本 VM | **32 位字流**，`低16位 = 操作码，高16位 = 内联参数`，变长操作数由 handler 自取 |
| 操作码 | 全量 **255** 条已恢复（分发容量 1024），`arity` 全部求解 |
| 文本 | 每个脚本块尾部一段 NUL 结尾字符串池，代码用**绝对字节偏移**引用 |
| 文本内控制码 | `0x0C` 结束 / `0x0D` 换行 / `0x0E` 页末 / `0x0F` 分段 / `0x05..0x07` 注音 / `0x19` 半角前缀 |
| 汉化方案 | **方案 A（固定偏移原地替换）**——池长度与所有偏移保持不变 |
| 译文编码 | 简体中文 → `hanzi2kanji_table.txt` 映射 → CP932 落盘（见 §5 汉化层） |
| 零突变 | 反汇编↔汇编 **509/509** 逐字节一致；封包解↔封 **三章 BIT-PERFECT** |

**三章文本量**

| 章 | 脚本块 | 池字符串 | 可译文本 | 唯一文本 | 实际改动 |
| --- | --- | --- | --- | --- | --- |
| 1 | 106 | 4 553 | **3 153** | 3 095 | 3 097 |
| 2 | 201 | 16 035 | **12 533** | 12 028 | 12 444 |
| 3 | 202 | 16 069 | **12 521** | 12 019 | 12 431 |
| 合计 | 509 | 36 657 | **28 207** | 27 142 | 27 972 |

> 「实际改动」= 与日文原串不同的条目数。未改动的 56 / 89 / 90 条绝大多数是
> **角色名**（本身已是汉字，按约定保持原样）。

**质量保证流程**

1. 批量机翻（`translate.py`，去重 + 缓存 + 容错解析 + 失败二分重试）；
2. **回显修复**（`retry_echo.py`）：挑出模型原样照抄日文的条目重译
   —— 第 1/2/3 章分别修复 34 / 630 / 427 条；
3. **人名修复**（`names_map.py`）：首轮提示词要求「人名保持原样」，导致
   `らいん / セカンド / ひぃちゃん / ぴよちゃん / オーキ / カンナ / マイマイ /
   ミカリンゴ / なる / はる` 及 `さん / ちゃん / くん` 敬语留在译文里；
   现按固定译名表确定性替换（详见 §5.4）；
4. **残留假名重译**（`retranslate.py`）：`message == pre_jp` 检不出「只把 僕→仆」
   这类假翻译，改为「译文里仍有假名」判据，重译 15 / 66 / 57 条，
   全库残留假名条目 771 → **3**（且都是「嘴巴保持『ひ』的口型」这种必须保留的写法）；
5. 后处理（`finalize.py`）：**繁体归一 → 字位映射 → 人名替换 → CP932 校验**，
   三章均「无非 CP932 字符」；
6. **长度修复**（`repair_length.py`）：确定性压缩 → 模型压缩 → 裁尾，
   三章最终**超预算条目 0**。

---

## 2. 逆向过程（每一步的证据链）

### 2.1 定位引擎

EXE 中残留源码路径字符串，一步锁定模块划分：

```
.\src\system\core\windows\SysFilePack.cpp     封包
.\src\system\core\common\SysFPlus.cpp         封包内存视图
.\src\system\script\FileLoad.cpp              CoD.cpt 装载
.\src\system\script\GsxVm.cpp                 VM 主循环
.\src\system\script\GsxVm_MsgProcs.cpp        消息/文本指令
.\src\system\script\GsxVm_GraphicsProcs.cpp   图形指令
.\src\system\script\sys_scmes*.cpp           台词显示
.\src\system\script\sys_scname.cpp           人名显示
```

### 2.2 封包

`strncmp` 判定函数（`0x0042f1c0`）→ 调用者 7 处 → 其中 `SysFilePack.cpp` 的
4 个函数（`0x40b320/0x40b600/0x40b7a0/0x40b8a0`）给出「按名字查找 + 定位文件偏移」
的完整逻辑，从而反推出格式 A 的头部与 12 字节记录表。
`0x0040b470` 读取文件前 0x14 字节、以偏移 `0x08` 作为元数据长度，最终确定
**名字表 16 字节对齐、数据块 16 字节对齐**两处对齐规则（`fpack verify` 可证）。

### 2.3 剧本容器

EXE 字符串 `script/CoD.cpt` → 引用者 `0x00457c60` → 其中：
`FUN_00457b60 → FUN_00466800`（差分替换解密）、`FUN_00457c20`（顺序读取）、
`FUN_00457d50`（容器解析）、`FUN_00466660/b0`（按字数分配 code/data 缓冲）。

### 2.4 VM

`0 < StackPointer` 断言 → `0x00458430`（POP）→ 同一模块内找到 PUSH
（`0x00458330`）与 RETURN（`0x00458f00`）。
`0x00437800` 内的 `call [0x00ed2ec0 + op*4]` 直接给出**解释器主循环**与
「低 16 位操作码 / 高 16 位内联参数」的编码约定；
`0x00459520` 一次性填充 1024 项分发表，用机器码 `C7 05 <addr> <imm32>` 扫描即
可无损恢复 255 条操作码 → 处理函数映射。

### 2.5 指令边界（本引擎最难的一点）

因为 handler 自己取操作数，指令变长且**没有任何长度表**。
工程上采用「启发式初值 + 约束求解」：

1. 统计每个 handler（含非 handler 的被调函数）中 `call 0x00466700` 的调用点数；
2. 以「全部 509 个脚本必须恰好消耗 `code_words` 个字」为约束，
   用逐脚本迭代 DFS + 爬山法修正 arity 表。

最终 **509/509** 脚本可精确切分 → 反汇编器可以给出真正的指令级视图，
且汇编器可逐字节还原。

---

## 3. 目录结构

```
Beautiful World/
├─ Beautiful World 1.1/          原始第一章（只读）
├─ Beautiful World 1.2/          原始第二章（只读）
├─ Beautiful World 1.3/          原始最终章（只读）
├─ hanzi2kanji_table.txt         字位映射表（只读，未修改）
├─ docs/
│   ├─ vm_analysis.md            VM 分析定义文档（唯一真值源）
│   └─ engine_report.md          本报告
├─ tool/                         全部工具（Python 3，无第三方依赖）
│   ├─ opcodelist.py             255 条操作码字典（自动生成）
│   ├─ disassembler.py           → gsx_disasm.py 的兼容入口
│   ├─ assembler.py              → gsx_asm.py 的兼容入口
│   ├─ gsx_disasm.py             反汇编器：CoD.cpt → *.asm.txt
│   ├─ gsx_asm.py                汇编器：*.asm.txt → *.bin
│   ├─ fpack.py                  封包工具 unpack/pack/verify/list
│   ├─ cod.py                    CoD.cpt 容器 + 加解密
│   ├─ gsx_text.py               文本提取/注入（extract/inject/names/stats）
│   ├─ translate.py              批量机翻（中转站 + 缓存 + 容错）＋ HanziMap 映射核心
│   ├─ retry_echo.py             回显条目重译（禁止照抄日文）
│   ├─ retranslate.py            残留假名条目二次重译
│   ├─ finalize.py               译文后处理（繁体归一 + 槽位映射 + 人名 + CP932）
│   ├─ names_map.py              人名/敬语固定译名表（确定性替换）
│   ├─ repair_length.py          超长译文压缩（确定性词典 + 模型压缩）
│   ├─ build_release.py          注入 + 重新封包 + 组织发布目录
│   ├─ run_chapter.py            一章端到端流水线
│   ├─ solve_arity.py / deriv_arity.py / build_opcodes.py / gen_opcodelist.py
│   ├─ verify_roundtrip.py       零突变验证
│   ├─ verify_release.py         成品校验（逐条回读 / 未译部分比对）
│   ├─ check_font_slots.py       ★ 字体槽位自检（模拟渲染比对 / 残留假名 / 非 CP932）
│   ├─ check_message_syntax.py   ★ 消息语法自检（0x19 半宽前缀 / 裸 ASCII 指令）
│   ├─ trad2simp.txt             繁体→简体对照（6688 条，由 zhconv 生成）
│   ├─ t2s_extra.txt             人工补充/覆盖的繁体归一（鸫→鸨、篠→筱、笹→竹…）
│   └─ jis_supplement.txt        槽位表补充映射（当前为空，仅保留手工条目位）
├─ work/
│   ├─ ch1|ch2|ch3/
│   │   ├─ unpack/               解包产物
│   │   ├─ asm/                  反汇编产物（*.asm.txt）
│   │   ├─ rebuild_asm/          汇编回写产物（零突变验证用）
│   │   ├─ text/                 jp.json / zh.json / *.meta.json / cache.json
│   │   └─ release/              该章的重新封包产物
│   └─ *.log                     运行日志
└─ release/                      ★ 三章汉化成品（可直接覆盖回游戏目录）
    ├─ Beautiful World 1.1/
    ├─ Beautiful World 1.2/
    └─ Beautiful World 1.3/
```

---

## 4. 工具使用说明

### 4.1 封包工具 `fpack.py`

```bash
python tool/fpack.py list   "Beautiful World 1.1/filepack.bin"
python tool/fpack.py unpack "Beautiful World 1.1/filepack.bin" work/ch1/unpack
python tool/fpack.py pack   work/ch1/unpack work/ch1/repacked.bin
python tool/fpack.py verify "Beautiful World 1.1/filepack.bin"        # 解包→封包→比对
python tool/fpack.py info   "Beautiful World 1.1/filepack.bin"
```

* 自动识别格式 A（带名字表）/ 格式 B（纯索引）；
* 解包时输出 `_manifest.json`（含头部常量、条目顺序、每项大小）与 `_order.txt`；
* `verify` 对三章实测 **BIT-PERFECT**。

### 4.2 剧本容器 `cod.py`（库）

```python
import cod
tbl = cod.load_table('<章>.exe')          # 自动定位置换表
inv = cod.make_inverse(tbl)
raw, _ = cod.get_file('filepack.bin', '/Data/script/CoD.cpt')
dec = cod.decrypt(raw, tbl)               # 解密
magic, count, scripts = cod.parse_cod(dec)
new = cod.build_cod(count, blocks)        # 重建
enc = cod.encrypt(new, tbl, inv)          # 回加密
```

### 4.3 反汇编 / 汇编（零突变）

```bash
python tool/gsx_disasm.py "Beautiful World 1.1" -o work/ch1/asm      # 每脚本一个 .asm.txt
python tool/gsx_asm.py    work/ch1/asm -o work/ch1/rebuild_asm       # 批量汇编
python tool/verify_roundtrip.py                                      # 三章 509/509 校验
```

`asm.txt` 片段（`01_002`）：

```
.script 01_002
.encoding cp932
.code 123

loc_c00000000:
    MSG_290        0x00000001
    GRA_82         0x0000000B, 0x00000064, 0x00000000, 0x00000001
    MSG_296        0x000000AB
    MSG_114        0x00000000    ; 0x0="自殺を考えたことはない。{{0C}}{{0D}}"
...
.end_code

.data 1164

str_00000000: "自殺を考えたことはない。{{0C}}{{0D}}"
str_0000001B: "――と、言うのはウソ。{{0C}}{{0D}}"
.end_data
```

* 助记符 = `<模块缩写>_<操作码>`（模块由 EXE 中 assert 字符串 + 代码邻接推断）；
* 操作码字带非 0 内联参数时写作 `MNEMONIC@0x…`；
* **不出现任何十六进制转储**：不可显示字节一律 `{{XX}}` 占位；
* 注释里内联显示被引用的池字符串，编辑文本无需翻到文件尾部。

### 4.4 文本提取 / 注入 `gsx_text.py`

```bash
python tool/gsx_text.py stats   "Beautiful World 1.1"
python tool/gsx_text.py extract "Beautiful World 1.1" \
        -o work/ch1/text/jp.json --meta work/ch1/text/jp.json.meta.json --chapter 1.1
python tool/gsx_text.py names   "Beautiful World 1.1" -o work/ch1/text/names_jp.json
python tool/gsx_text.py inject  "Beautiful World 1.1" \
        --json work/ch1/text/zh.json --meta work/ch1/text/jp.json.meta.json \
        -o work/ch1/build/CoD.cpt --force
```

* 输出遵循备忘录规定的 JSON 规范（`id / name? / pre_jp / message`）；
  本引擎没有说话人字段（人名由独立指令 + 独立池串给出），因此另外导出
  `names_jp.json` 作为术语表；
* `*.meta.json` 记录每条的 `script / offset / slot / prefix / suffix / budget`，
  供注入器精确回写；
* 池内只改写被翻译的串，**其余字节不动**。

### 4.5 翻译 `translate.py` + `finalize.py`

```bash
python tool/translate.py work/ch1/text/jp.json -o work/ch1/text/zh.json \
       --cache work/ch1/text/cache.json --batch 120 --workers 10
python tool/finalize.py  work/ch1/text/jp.json --cache work/ch1/text/cache.json \
       -o work/ch1/text/zh.json
```

* 中转站：`https://pro.gemai.cc`，模型 `deepseek-v4-flash`；
* 去重 + 磁盘缓存（可断点续跑）；JSON 容错解析；批量失败自动二分重试；
* 后处理顺序（`finalize.py`）：
  `normalise_markers` → **繁体归一（`trad2simp.txt` + `t2s_extra.txt`）**
  → **槽位映射（`hanzi2kanji_table.txt`）** → **人名/敬语替换（`names_map.py`）**
  → `fix_punct` → CP932 校验。
  先归一后查表这一步是必需的，原因见 §5.2。

> **中转站关键坑（决定性）**：该站接受 `thinking={"type":"disabled"}` 但**不生效**。
> 不使用 `reasoning_effort` 时，一旦请求内台词超过约 35 条，模型会把整个
> `max_tokens` 全部花在隐藏推理上（`reasoning_tokens=8192`），`content` 返回**空串**，
> `finish_reason="length"`——这是持续性失败而非偶发抖动。
> 实测**只有 `reasoning_effort="none"` 能真正关闭推理**：

| 参数 | 60 条请求 | content | reasoning_tokens |
| --- | --- | --- | --- |
| 无 | 59 s | 空 | 8 192 |
| `thinking={"type":"disabled"}` | 57 s | 空 | 8 192 |
| `enable_thinking=false` | 61 s | 空 | 8 192 |
| **`reasoning_effort="none"`** | **12 s** | **正常** | **0** |

### 4.6 回显修复 `retry_echo.py`

模型偶尔会把某些条目**原样照抄日文**（短句、拟声、呻吟、残缺句尤甚）。
本工具挑出 `message == pre_jp` 且原文含假名的条目，用「绝对禁止照抄日文」的
强硬提示词分小批重试，再逐条重试一轮：

```bash
python tool/retry_echo.py work/ch1/text/jp.json --cache work/ch1/text/cache.json \
       --batch 16 --workers 6
```

### 4.7 超长译文压缩 `repair_length.py`

方案 A 下每条文本有固定字节预算。工具先跑**确定性压缩词典**
（`已经→已`、`什么→啥`…，再丢句末标点），仍超长的交给模型「在保留原意的前提下
压缩到 N 个字符以内」，最后一轮才按字符边界裁尾：

```bash
python tool/repair_length.py work/ch1/text/jp.json \
       --meta work/ch1/text/jp.json.meta.json --zh work/ch1/text/zh.json \
       --rounds 3 --batch 25          # 加 --no-llm 则只跑确定性压缩
```

### 4.8 人名/敬语修复 `names_map.py` + 残留假名重译 `retranslate.py`

```bash
python tool/finalize.py work/ch1/text/jp.json --cache work/ch1/text/cache.json \
       -o work/ch1/text/zh.json          # 已内置 names_map
python tool/retranslate.py               # 三章；或 python tool/retranslate.py ch2
```

* `names_map.py` 是**确定性字符串替换**，译名表见 §5.4；
* `retranslate.py` 把「译文里仍有假名」的条目抽出来单独重译，
  写回 `cache.json`，随后再跑一次 `finalize.py` 即可。

### 4.9 槽位自检 `check_font_slots.py`

```bash
python tool/check_font_slots.py     # 审计显示结果 / 残留假名 / 非 CP932
```

判据：把 `cache.json`（模型原文）重现一遍映射得到「本该显示的文字」，
再与成品 `zh.json` 的**实际显示结果**逐字符比对，多出来的字形就是错字。
返回码 0 表示干净，可直接接入 CI。

> ⚠️ 映射表只能作用一次。**不要**把 `zh.json` 再喂进 `HanziMap` 刷第二遍：
> `凜` 显示「你」，但 `凜` 同时也是「凛」的槽位，二次映射会把它换成别的码位，
> 屏幕上的「你」就变成了别的字。要重刷就重跑 `finalize.py`（`cache.json` 是
> `zh.json` 的前像：`repair_length.py` 会把压缩结果写回 cache）。

### 4.10 一章端到端 `run_chapter.py`

```bash
python tool/run_chapter.py 1        # 1 / 2 / 3
```

依次执行：解包 → 提取 → 机翻 → 回显修复 → 后处理（含人名）→ 长度修复 → 注入 →
重新封包 → 反汇编/汇编 → 成品校验。

### 4.11 端到端重建 + 验证

```bash
python tool/verify_roundtrip.py                                   # 反汇编↔汇编 509/509
python tool/fpack.py verify "Beautiful World 1.1/filepack.bin"    # 封包 BIT-PERFECT
python tool/verify_release.py "release/Beautiful World 1.1" \
       --meta work/ch1/text/jp.json.meta.json --zh work/ch1/text/zh.json \
       --orig "Beautiful World 1.1"                               # 成品逐条回读
python tool/check_font_slots.py                                   # 字体槽位审计
```

---

## 5. 汉化交付说明

`release/` 下每个目录就是**可直接覆盖回游戏目录**的完整游戏：

```
release/Beautiful World 1.1/
├─ Beautiful World １章.exe      （原样保留）
├─ filepack.bin                  ★ 已注入中文剧本
├─ Data/  SaveData/  readme.TXT  epuninst.exe   （原样保留）
```

**用法**：把 `release/Beautiful World 1.1/` 的内容覆盖到原游戏目录（或直接把该
目录当作游戏目录运行），启动 EXE 即为中文版。

### 5.1 自检清单（每次重建后跑一遍）

```bash
python tool/verify_roundtrip.py                                  # 509/509
python tool/fpack.py verify "release/Beautiful World 1.1/filepack.bin"
python tool/verify_release.py "release/Beautiful World 1.1" \
       --meta work/ch1/text/jp.json.meta.json --zh work/ch1/text/zh.json \
       --orig "Beautiful World 1.1"
# 字体槽位自检：不该出现的「槽位字」与残留假名
python tool/check_font_slots.py
```

`check_font_slots.py` 会用 `hanzi2kanji_table.txt` 反查每个已写入字符的
**实际显示结果**（见 §5.2），与「从模型原文重现出来的预期文字」逐字符比对。
当前三章结果：**屏幕多余字形 0，繁体残留 0，非 CP932 0，
残留假名 3 条（均为「嘴巴保持『ひ』的口型」这类必须保留处）**。

### 5.2 `hanzi2kanji_table.txt` 的真实语义（关键）

这张表**不是「简→日字形对照」，而是一张字体槽位分配表**：

```
K <TAB> V   ⇒  在游戏里写出 V，屏幕上显示的是 K 的字形
```

依据：
* 3019 条里有 2972 条的「键」**根本无法用 CP932 编码**（这/说/为/你/们/她…），
  也就是说「写出键」这件事本身不可能发生，键只能是「想显示的字」；
* 全部 3019 个「值」都可以用 CP932 编码，且互不重复（单射）；
* 实证：`边→邊` 写出 `邊` 显示「边」；`总→総` 写出 `総` 显示「总」；
  `伥→櫻` 写出 `櫻` 显示「伥」。

**推论（第一版汉化的两处翻车原因）**

1. 模型输出里夹带了大量**日文/繁体字形**（來 會 個 點 說 櫻 遊 沒 …）。
   其中一批恰好也是别人家的「槽位」：`礌→來`、`苁→會`、`鲟→個`、`爸→點`、
   `吡→遊`、`毽→沒`、`伥→櫻`…… 于是写出 `來` 会显示成「礌」、写出 `櫻`
   会显示成「伥」——**玩家看到的就是满屏不相干的怪字**，
   `櫻坂` 显示成 `伥坂` 正是此因。
2. 第一版 `finalize.py` 只做「查表替换」：模型输出的简体字命中键就替换，
   繁体字不命中就原样落盘，于是第 1 点无法被拦住。

**v2 修法**（`translate.py::HanziMap`）：

```
输入字符 c
  ├─ c 命中主表的键           → 直接写出对应槽位
  ├─ c 是繁体/日文字形（trad2simp.txt 有映射）
  │     → 先归一成简体 s；s 命中键 → 写槽位；
  │       s 可编码且不是别人的槽位 → 写 s
  └─ 其余原样写出
最后兜底：若结果无法 CP932 编码（如 鸫/筿），退回可编码的繁体形（鴇/篠）
```

因此新增/改动：
* `tool/trad2simp.txt` —— 由 `zhconv` 生成的 6688 条繁→简对照（离线数据，
  工具本身不依赖 `zhconv`）；
* `tool/t2s_extra.txt` —— 人为补充（`神→神` 等兼容汉字）；
* `tool/jis_supplement.txt` —— 清空（v1 里那 65 条「语义近似替换」在 v2 下
  反而是污染源）。

### 5.3 消息文本的字节级规则（会直接导致崩溃，必须遵守）

引擎的 `sys_scmes.cpp` 里 `FUN_0046c460(msg)` 是**逐字节**解析消息文本的：

| 字节 | 含义 |
| --- | --- |
| `0x01`–`0x1F` | 控制码（`0x0C`/`0x0E` 翻页，`0x0D`/`0x0F` 换行/分段，`0x05`–`0x07` 注音…） |
| **`0x19`** | **「后面一个字节是单字节字符」**（解析器推进 2 字节） |
| **ASCII `0x20`–`0x31`** | **面部件/姿势指令**（`!` `"` `$` `%` `&` `'` `(` `)` `*` `+` `,` `-` `.` `/` `0` `1` 以及空格、`#`） |
| 其它 | 按 cp932 一个双字节字渲染，解析器同样推进 **2 字节** |

两条由此而来的硬性要求：

1. **每个单字节字符（ASCII 与半宽片假名）都必须写成 `0x19 + 该字节`**。
   否则解析器会把下一个字节一起吃掉 → 错位 → 最终撞到 `0x20..0x31` 里某个字节，
   被当成指令执行。其中 `*` `+` `,` `-` `.` `/` `1` 会断言 `isSetupFacePose`
   （`sys_scmes.cpp:6141`），玩家看到的就是
   **`System Asssert : Cond : isSetupFacePose`** 弹窗并崩溃。
2. **不能保留「原文开头/结尾的 `0x19`」**。原文若以半宽字符开头（如 `19 31 19 30年`
   ＝「10年」），提取时会把首个 `0x19` 记成「前缀控制码」；译文改成以全角字开头
   （「大約10年前」）后，这个 `0x19` 就变成孤立前缀 → 解析器跳过译文第一个字的
   首字节 → 错位 → 触发第 1 条同样的断言。
   修法：`split_controls` 不再把 `0x19` 当字符串级前缀/后缀；`from_display()` 会为
   每个单字节字符重新补 `0x19`，所以信息不丢。

另：**说话人名表（`init_app`）不走消息解析器**，原文在那里存的是**裸 ASCII**
（`学生A` 存成 `8a77 90b6 41`）。所以注入人名表时用 `from_display_raw()`，
不加 `0x19`（否则名字框里会多出一个控制字形）。

`tool/check_message_syntax.py` 按上表把三章 28 238 条消息串全部走一遍：
**当前结果 0 条不合规**（原始数据反而有 4 条「裸 ASCII」，全在人名表里，已按同样规则放行）。

### 5.4 人名与敬语

首轮提示词写的是「人名保持原样不译」，所以 `らいん / セカンド / ひぃちゃん /
ぴよちゃん / オーキ / カンナ / マイマイ / ミカリンゴ / なる / はる` 这些假名人名
以及 `さん / ちゃん / くん` 敬语原封不动留在了中文句子里。
v2 的处理：**`tool/names_map.py` 用固定译名表确定性替换**（改提示词只对将来
重译有效，28 207 条全量重译没必要），译名固定为：

| 原文 | 译名 | 说明 |
| --- | --- | --- |
| らいん | 莱茵 | 女主角名字，全篇假名 |
| セカンド | 赛肯德 | 研究室代号（原作即外来语） |
| ひいちゃん / ひぃちゃん | 小希 | 昵称，本名「霧谷ひ…」→ 雾谷希 |
| ぴよちゃん | 小阳 | 昵称，原作明确「ぴよちゃん＝如月（陽々子）」 |
| オーキ（先輩） | 奥基（前辈） | 全名 タカシタオーキ＝高下奥基 |
| カンナ / マイマイ / ミカリンゴ | 佳奈 / 舞舞 / 美香苹果 | 原作自解：佳奈子 / 舞衣 / 美香 |
| ナル / ハル / ときのん | 小鳴 / 小春 / 小鴇乃 | 鳴神玲 / 告春 / 小鳥遊鴇乃 的昵称 |
| くるみ（ん） | 来未 | 薪叢来未 的读音 |
| さん / ちゃん / くん | 同学 / 酱 / 君 | 与模型自身在别处的译法保持一致 |

另外 `retranslate.py` 会把「译文里仍有假名」的条目单独重译
（首轮「照抄日文」的条目往往只改了 僕→仆 一两个字，
`message == pre_jp` 这种判据抓不到）。

---

## 6. 已知限制 / 后续可做

1. **EXE 内的界面文字未汉化。** 菜单/按钮/错误提示字符串硬编码在
   `.rdata`（如 `セーブ`@`0x0cc294`、`ロード`@`0x0cc29c`、`音量`@`0x0cb80c`、
   `画面`@`0x0c7308`、`既読`@`0x0cbc8d`、`苗字/名前`@`0x0cd30c`…），
   数量很少（约 30 条）。若要一并汉化，需要按「等长或更短 + 空格补齐」的
   方式原地改写 EXE——本工程未做，以免引入崩溃风险。
2. **字体。** 译文按用户约定写入日繁字形码位，需要用户的自制 JIS 映射字体
   才能正确显示简体字形；引擎未内嵌该字体，请按原方案安装/挂载。
3. **超出原长度的译文会被截断。** 方案 A 下每条文本有固定预算
   （见 `*.meta.json` 的 `budget`）。中文普遍短于日文，实测超预算比例极低，
   截断条目已在构建日志中逐条列出。
4. **注音（ruby）信息丢失。** 原作的 `0x05/0x06/0x07` 注音标记按中文习惯删除，
   只保留基文。
5. **`arity` 表为求解结果。** 509/509 脚本可精确切分是可验证的事实，但不排除
   存在极少数语义不同而长度巧合相同的切分；反汇编视图用于阅读与审阅，
   实际注入不依赖它（注入只改字符串池）。
