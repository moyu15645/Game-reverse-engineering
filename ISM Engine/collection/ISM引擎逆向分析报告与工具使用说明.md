# ISM 引擎逆向分析报告 + 工具完全使用说明

> 项目：《タイムカプセル “春”》（Time Capsule "Spring"）脚本引擎汉化
> 引擎：ISM Script VM（栈式脚本虚拟机，由 `ism.dll` 承载）
> 目标：解包 → 反汇编 → 提取文本 → 翻译 → 注入 → 回封 → 游戏实测，全程零突变

---

## 一、项目概览

| 项 | 内容 |
| --- | --- |
| 游戏主程序 | `tcs.exe`（仅为主程序壳，不承载脚本逻辑） |
| 脚本引擎 | `ism.dll`（真正的脚本 VM 载体） |
| 脚本文件 | `.ISM`（剧情脚本 + 系统脚本，共 38 个） |
| 资源封包 | `data.isa`（magic `"ISM ARCHIVED"`，806 个文件） |
| 文本编码 | Shift-JIS（cp932） |
| 翻译方案 | 日文 → 简体中文 → 「日繁」映射（保证 cp932 可编码，免改字体 DLL） |

**汉化策略**：正文走系统 GDI（`CreateFontA` + `GetGlyphOutlineA`），`iCharSet=0x80`(SHIFTJIS)。简体中文字符不在 cp932 内，所以用 `hanzi2kanji_table.txt` 把简体逐字映射为日文汉字（如 你→凜、吗→龜、她→瑤、说→説），这样**不改 ism.dll** 就能显示中文。

---

## 二、引擎逆向分析报告

> 本节是工具链的唯一真值源（详见 `ism_tools/vm_analysis.md`），由 `ism.dll` 反汇编佐证。

### 2.1 VM 架构

| 项 | 结论 |
| --- | --- |
| 架构 | **栈式虚拟机**（stack-based），单字节主操作码 |
| 指令流 | **冯·诺依曼式**：操作数与内联文本和指令流同区（代码区），独立数据仅 switch 跳转表 |
| 指令指针 IP | `[0x100640e4]` |
| 数据栈指针 | `[0x100680f8]` |
| 当前帧 / 当前脚本 | `[0x100b9254]` / `[0x100b9258]` |
| 分发器 | 主循环 `0x1000145d`，经 `call [ecx*4 + 0x100a4a38]` 动态分发 |
| 静态 opcode 表 | `0x10037420`：**123 个 opcode → 113 个唯一 handler**，以 `0xFFFFFFFF` 终止 |

**执行范式**：分发器取当前 IP，读 1 字节 opcode，查表得 handler 地址并调用；handler 自行推进 IP 并返回。多数 handler 推进 IP 后检查 `byte ptr [IP] == 0x05`，相等则再 `inc IP` 一次——**字节 `0x05` 是语句分隔符/终止符（SEP）**，本身也可作为独立 opcode。

### 2.2 ISM 文件格式（头部布局）

文件头 magic：`"ISM SCRIPT\x00\x00"`（12 字节）。所有字段小端：

| 偏移 | 字段 | 类型 | 说明 |
| --- | --- | --- | --- |
| `0x00` | magic | 12B | `"ISM SCRIPT\x00\x00"` |
| `0x0C` | file_size | u32 | 文件总大小 |
| `0x10` | code_base | u32 | 代码区起点，**等于头部结束偏移（header_end）** |
| `0x14` | count_a | u32 | 记录数 A，**不恒等于 count_d**（须原样保留） |
| `0x18` | unk_18 | u32 | 恒等于 num_entries |
| `0x1C` | num_entries | u32 | 入口表条数 |
| `0x20` | entries[] | 12B/条 | 入口表，每条 `(off, p1, p2)`，off 为 code-relative |
| — | count_d | u32 | 函数表条数 |
| — | count_1 | u32 | 记录数 1（通常 = count_d，差值 = 具名全局变量条数） |
| — | count_2 | u32 | 记录数 2 |
| — | funcs[] | 变长 | 函数表，每条 `(off, p1, p2, name_len, name+NUL)`，大小 = name_len + 14 |
| — | unk_y0 | u32 | 恒等于 count_e |
| — | count_e | u32 | 字符串表 e 条数 |
| — | count_f | u32 | 字符串表 f 条数（本批样本全为 0） |
| — | strings_e[] | 变长 | 每条 `len + bytes + NUL`（大小 = len + 2） |
| — | strings_f[] | 变长 | 同上（count_f=0 时无） |
| — | labels[] | 变长 | 可选，`0xFF 0x01` + count + 每条 `(off,p1,p2,len,name+NUL)` |

**布局不变量**：`header_end == code_base`（反汇编器会断言校验）。

**具名全局变量**：函数表里 `off & 0x80000000` 置位的条目不是代码偏移，而是 `0x80000000 | variable_index` 形式的具名全局变量（如 MAINSYS 的 `mouthposition`、`specialCharActive`），须原样保留，不得映射为代码标签。

### 2.3 字符串内联加密算法（op_33 STR / op_45 STRREF）

内联字符串逐字节加密：`not dl; xor dl, key`，即 **明文 = (~密文) & 0xFF ^ key**。

**密钥** = `(opcode 字节的文件偏移 - code_base) & 0xFF`，若等于 `0xFF` 则归零。

- **STR（0x33）**：`key = (opcode_offset - code_base) & 0xFF`
- **STRREF（0x45）**：引用代码区某 STR（code-relative 偏移 ref），`key = ref & 0xFF`

**长度编码**：长度字节 `< 0xFF` 时用 1 字节（数据从 `IP+2`）；否则后接 u32 长度（数据从 `IP+6`）。

### 2.4 Opcode 长度表

| 长度 | opcode |
| --- | --- |
| **1 字节**（定长，104 条） | 除下列外的全部 opcode |
| **2 字节** | `0xF3`（SYS_F3，u8 子码） |
| **5 字节**（u32 操作数） | `0x20 0x21 0x23 0x24 0x30 0x31 0x38 0x3A 0x3C 0x3E 0x45` |
| **6 字节**（u8 + u32） | `0x39 0x3B 0x3D 0x3F` |
| **9 字节**（双 u32） | `0x0F`（SWITCH）、`0x32`（OP_32） |
| **变长** | `0x33`（STR：长度字节 `<0xFF` 时总长 `2+len`，否则 `6+len`） |

### 2.5 Opcode 分类概览

完整 123 个 opcode 定义见 `opcodelist.py`（唯一实现依据）。分类：

| 类别 | opcode | 助记符 |
| --- | --- | --- |
| 分隔/空操作 | `0x00 0x22 0x2B` | NOP |
| | `0x05 0x06` | SEP（语句分隔符） |
| 算术 | `0x0B~0x15 0x1C 0x1D` | SHIFT/LAND/LOR/XOR/ADD/SUB/MUL/DIV/MOD/BAND/BOR |
| 比较/逻辑 | `0x16~0x1B 0x1E 0x1F` | EQ/LT/GT/NE/LE/GE/NOT/NEG |
| 控制流 | `0x0F` | SWITCH（range/sparse 跳转表） |
| | `0x20 0x24` | JMP；`0x21` JZ；`0x25` RET；`0x28` CALL；`0x10` INVOKE |
| 栈/变量 | `0x23 0x38 0x39` | PUSH_VAR；`0x30 0x31` PUSHI；`0x3A~0x3F` SET_VAR |
| 字符串 | `0x33` | STR（内联加密字符串）；`0x45` STRREF |
| 消息/控制 | `0x29 0x2A 0x2C 0x2D 0x2E 0x2F 0x50~0x52 0x60 0x61` | END/FRAME/MSG/LOOP 等 |
| 系统调用 | `0x80–0xFF` | SYS_xx（引擎内建 API） |

> 同一助记符可能对应多个 opcode（JMP=0x20/0x24、PUSHI=0x30/0x31 等）。为保证**零突变 round-trip**，反汇编器对歧义助记符自动追加 `_XX` 后缀（`JMP_20`/`JMP_24`），汇编器据此精确还原字节。

### 2.6 控制流与跳转基准

| 指令 | 操作数 | 基准 | 公式 |
| --- | --- | --- | --- |
| JMP `0x20/0x24` | rel32（有符号） | 指令自身偏移 | `target = instr_off + rel` |
| JZ `0x21` | rel32（有符号） | 指令自身偏移 | `target = instr_off + rel` |
| SWITCH `0x0F` | table_off(u32) + count(u32) | 指令自身偏移 | `target = instr_off + rel` |
| STRREF `0x45` | ref(u32) | **code_base** | `target = code_base + ref` |
| 入口/函数/标签 off | off(u32) | **code_base** | `target = code_base + off`（高位未置位时） |

### 2.7 switch 跳转表格式（关键）

SWITCH（`0x0F`）9 字节：`[0x0F][table_off u32][count u32]`。`table_off` 为 code-relative，指向文件末尾独立数据区。

**代码区连续 `[code_base, code_end)`**，`code_end = code_base + min(table_off)`。所有 switch 表在代码区之后，线性扫描必须在此停止。

表首 u32 是 marker，分两种：

- **range 表**（marker `0xFF000000`）：`[0xFF000000][default_off i32][min u32][case_off i32 × count]`，大小 `12 + count×4`。
- **sparse 表**（marker `0xFE000000`）：`[0xFE000000][default_off i32][(key u32, off i32) × (count-1)]`，大小 `count×8`。
  - **关键**：sparse 的「pair 0」被 marker/default 占用，真正的 `(key, off)` 对从下标 1 到 count-1，共 **count-1** 个。

### 2.8 数据封包格式（ISA）

封包头 16 字节：magic `"ISM ARCHIVED"`(12B) + count u16 + flags u16。索引 entry 每条 32 字节：

```
name[16] + offset u32 + size u32 + r2 u32 + r3 u32
```

**文件名拼接**（关键）：`name[12]` 为基础名，若 `name[13] == 0x01` 表示有延续，文件名 = `name[12] + pack(r2) + pack(r3)`。所以剧情脚本磁盘上伪装成 `.I` 扩展名（如 `TC_SCN01_1.I`），解包后自动还原为 `.ISM`。

---

## 三、产出清单

### 3.1 核心工具（`ism_tools/`）

| 文件 | 作用 |
| --- | --- |
| `opcodelist.py` | 123 个 opcode 完整定义（含歧义助记符后缀、MNEMONIC_TO_OP 反向映射） |
| `disassembler.py` | 反汇编器（头部解析、op_33 加解密、线性扫描、switch 表解码） |
| `assembler.py` | 汇编器（布局计算、标签重定位、头部/代码/switch 表编码、重加密） |
| `isa_tool.py` | 封包/解包 `data.isa`（list/unpack/pack/verify） |
| `ism_extract.py` | 从 `.ISM` 提取对白（人名/台词/语音 → JSON，含 orphan narration 扫描） |
| `translate.py` | LLM 批量翻译 → 简体 → 日繁映射（去重、断点续传、容错解析） |
| `ism_inject.py` | 把译文写回 `.ISM`（变长重布局 + 重加密 + name 池 patch） |
| `vm_analysis.md` | VM 格式真值源文档（本报告第二章节的完整依据） |
| `asm.txt` | SYSTEM.ISM 示例反汇编输出 |
| `README.md` | 工具链简明使用说明 |

### 3.2 辅助脚本（`_work/`）

| 文件 | 作用 |
| --- | --- |
| `name_zh.py` | 说话人标签中日对照表（60+ 条人名/称呼/动作描述） |
| `build_name_patches.py` | 扫描 JSON 收集 name 池 → 翻译 → 写缓存 |
| `fix_cache.py` | 一次性修复缓存的换行标记/未覆盖字符 |
| `final_check.py` | 注入前最终检查（换行对齐、cp932 可编码、回显） |
| `*.py`（diag_/test_/debug_ 等） | 逆向调试脚本（可忽略，仅过程产物） |

### 3.3 数据与产出

| 文件/目录 | 作用 |
| --- | --- |
| `data.isa` | 游戏资源封包（806 文件，含 38 个 .ISM + 图片 + 音频） |
| `data_zh.isa` | **汉化后的封包**（最终交付物） |
| `_unpacked/` | 解包目录（含 `_order.txt` 文件顺序 + `_isa_manifest.json` 原始索引） |
| `_text/*.json` | 提取的 38 个对白 JSON（含 trans/trans_kanji 译文字段） |
| `_work/translation_cache.json` | 翻译缓存（断点续传，含 name 池 patch） |
| `ism.dll` / `tcs.exe` | 引擎 DLL / 主程序（逆向参考，勿改） |
| `CLAUDE.md` | 反汇编/汇编工具原始规范 |
| `备忘录.txt` | 用户的工作记录（多引擎逆向笔记） |
| `D:/Enginee/Engine/hanzi2kanji_table.txt` | 简体→日文汉字映射表（3018 条，用户提供） |

---

## 四、工具完全使用说明

> 推荐 Python：`C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe`
> 所有命令在项目根目录执行。

### 4.1 isa_tool.py — 封包 / 解包

```bash
# 列出包内文件
python ism_tools/isa_tool.py list data.isa

# 解包到目录（生成 _order.txt 和 _isa_manifest.json）
python ism_tools/isa_tool.py unpack data.isa ./_unpacked

# 从目录封包（读 _order.txt 保持原始顺序）
python ism_tools/isa_tool.py pack _unpacked data_zh.isa

# 验证解包→封包 bit-perfect（SHA 一致）
python ism_tools/isa_tool.py verify data.isa
```

### 4.2 disassembler.py — 反汇编

```bash
# 单个脚本 → 语义化汇编文本
python ism_tools/disassembler.py _unpacked/TC_ED.ISM -o TC_ED.asm.txt

# 批量处理目录
python ism_tools/disassembler.py _unpacked --encoding cp932
```

输出语义化汇编：标签前有空行、特殊字节用 `{{XX}}` 占位、字符串可读、无 `\x` 转义。

### 4.3 assembler.py — 汇编

```bash
# 汇编文本 → 重建二进制
python ism_tools/assembler.py TC_ED.asm.txt -o TC_ED.rebuild.ism --encoding cp932

# 批量处理目录
python ism_tools/assembler.py _unpacked --encoding cp932
```

重建规则：头部大小/code_base/各表偏移按当前布局重算；op_33 字符串按新位置重加密；跳转/switch/字符串引用按标签重定位。

### 4.4 ism_extract.py — 文本提取

```bash
# 提取单个脚本 → JSON
python ism_tools/ism_extract.py _unpacked/TC_ED.ISM -o TC_ED.json

# 批量提取整个目录（每个 .ISM 生成一个 .json）
python ism_tools/ism_extract.py _unpacked -o _text

# 输出纯文本（逐行，便于人工浏览）
python ism_tools/ism_extract.py _unpacked/TC_ED.ISM --format txt
```

输出 JSON 结构：

```json
{
  "id": 1,
  "name": "亮のお母さん",
  "name_offset": "0x51D",
  "pre_jp": "で、どうだった、旅行は。",
  "message": "で、どうだった、旅行は。",
  "voice": "F_67B_001.ogg",
  "offset": "0x51D"
}
```

提取原理：显示消息由 MSG(0x2C) 触发，其前按固定顺序压入 `[人名][台词][语音]` 三个字符串引用；人名池首次用内联 STR、后续 STRREF 引用同一位置。已额外加 **orphan narration 扫描**（处理"参数在 MSG 之后 push"的旁白，如 TC_OP 的 "それから７年―――"）。

### 4.5 translate.py — 翻译

```bash
# 全量翻译 _text/*.json 的 message（去重 + 断点续传，缓存于 _work/translation_cache.json）
python ism_tools/translate.py

# 测试少量
python ism_tools/translate.py --limit 100

# 自定义批次/模型
python ism_tools/translate.py --batch 200 --model deepseek-v4-flash
```

流程：日文台词 → 简体中文（`trans` 字段）→ 日繁映射（`trans_kanji` 字段，cp932 可编码）。

**关键参数（已调优，勿轻易改）**：
- 模型 `deepseek-v4-flash`，中转站 `https://pro.gemai.cc`（OpenAI 兼容）
- **必须关推理** `thinking={"type":"disabled"}`（否则 reasoning 吃满 token 导致 content 空）
- **简短提示词 + 温度 0.5 + 批量 200**（复杂提示词会让关推理模型大批量回显原文）
- 内置：容错 JSON 解析、换行标记保护/还原、行首标点交换、残留假名清理、确定性人名替换

### 4.6 ism_inject.py — 注入

```bash
# 注入目录下所有 .ISM（读 _text/<name>.json 的 trans_kanji + name 池 patch）
python ism_tools/ism_inject.py _unpacked

# 单文件注入
python ism_tools/ism_inject.py _unpacked/TC_ED.ISM
```

- 内联 STR 直接改；共享 STRREF 改其目标 STR（去重后同一原文同一译文，无冲突）
- 复用 assembler 做变长重布局 + 按新位置重加密
- name 池 patch 从 `_work/translation_cache.json` 读 `name_zh_bytes_hex`（说话人标签翻译）

---

## 五、完整汉化流水线

```bash
# 1. 解包
python ism_tools/isa_tool.py unpack data.isa _unpacked

# 2. 提取文本 → JSON
python ism_tools/ism_extract.py _unpacked -o _text

# 3. 翻译 + 日繁映射（写 trans/trans_kanji）
python ism_tools/translate.py

# 4. 构建说话人标签补丁
python _work/build_name_patches.py

# 5. 注入译文 → 重建 ISM
python ism_tools/ism_inject.py _unpacked

# 6. 回封
python ism_tools/isa_tool.py pack _unpacked data_zh.isa

# 7. 游戏实测（把 data_zh.isa 改名 data.isa 替换进游戏目录，DOSBox 验证）
```

---

## 六、验证方法

| 验证项 | 命令/方法 |
| --- | --- |
| 封包 bit-perfect | `python ism_tools/isa_tool.py verify data.isa` |
| 脚本 round-trip | 对每个 .ISM 反汇编→重汇编，逐字节比对（本工程 38/38 通过） |
| 注入正确性 | 重新解包 data_zh.isa → ism_extract 提取 → 核对译文 |
| 非资源文件零改动 | 对比解包前后 PNG/ISG/OGG 等文件的 SHA |
| 译文残留检查 | 检查 trans_kanji 无假名残留、无 `\n。` 行首孤立标点 |

---

## 七、踩坑记录与教训（务必复用）

1. **验证 ISM 是否日文，必须 `decrypt_33` 解密后判断**——op_33 字符串是 XOR 加密的，用原始字节搜日文/统计假名会把密文当明文，误判成"中文版"。（本项目曾因此反复误判 data.isa 丢失）

2. **操作游戏原档前先 SHA256 备份**，光看文件大小和 mtime 不可靠（in-place patch 能保持大小不变，mtime 也可伪装）。

3. **关推理模型（thinking disabled）大批量翻译时，复杂提示词会触发整体回显原文**——用简短提示词即可稳定。

4. **换行标记是字面 `\n`（0x5C 0x6E）/ `\N`（0x5C 0x4E）两字节**，不是真实换行，翻译时必须占位符保护 + 还原，否则模型会改成真实 0x0A。

5. **`fix_leading_punct`（行首标点交换）必须在 `ensure_markers` 之后、且之后不再跑 `ensure_markers`**，否则标记重插会把标点挤回 `\n` 后面。

6. **sparse switch 表大小 = `count×8`**（不是 `8+count×8`），实际 (key,off) 对 = `count-1` 个。

7. **代码区边界**：`code_end = code_base + min(table_off)`，线性扫描必须在此停止，否则会把 switch 表字节误当 opcode。

8. **deepseek-v4-flash 关推理后是弱快速模型**：需配合确定性词典（语气词）+ 确定性人名替换（clean_names）+ 容错 JSON 解析（闭合引号后多余标点）兜底，才稳定。

---

*本文档 + `ism_tools/vm_analysis.md` + `ism_tools/opcodelist.py` 构成该引擎的完整逆向知识库。*
