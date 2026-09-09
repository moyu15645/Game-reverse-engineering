# ISM 脚本虚拟机（ISM Script VM）分析定义文档

> 本文档是 ISM 脚本字节码反汇编 / 汇编工具链（`opcodelist.py`、`disassembler.py`、`assembler.py`）的**唯一真值源**。
> 所有字段布局、opcode 长度、跳转基准、加解密算法均以本文档为准，并由 `ism.dll` 的反汇编佐证。

---

## 1. VM 类型识别与运行环境

| 项目 | 结论 |
| --- | --- |
| 宿主 | `tcs.exe`（仅为主程序壳，不承载脚本逻辑） |
| VM 载体 | `ism.dll` |
| 脚本文件 | `.ISM`，打包于 `data.isa` 封包（magic `"ISM ARCHIVED"`，索引 entry 每条 0x20 字节） |
| 架构 | **栈式虚拟机**（stack-based），单字节主操作码 |
| 指令集 | **冯·诺依曼式**：操作数与内联文本与指令流同区（代码区），独立数据仅 switch 跳转表 |
| 关键状态 | 指令指针 IP `[0x100640e4]`；数据栈指针 `[0x100680f8]`；当前帧 `[0x100b9254]`；当前脚本 `[0x100b9258]` |
| 分发器 | 主循环 `0x1000145d`，经 `call [ecx*4 + 0x100a4a38]` 动态分发 |
| 静态表 | `0x10037420`：**123 个 opcode → 113 个唯一 handler**，以 `0xFFFFFFFF` 终止 |

**执行范式**：分发器从 `[0x100640e4]` 取当前 IP，读 1 字节 opcode，查静态表得到 handler 地址，调用 handler；handler 自行推进 IP 并返回。多数 handler 在推进 IP 后检查 `byte ptr [IP] == 0x05`，若相等则再 `inc IP` 一次——**字节 `0x05` 是语句分隔符 / 终止符**（SEP），本身也可作为独立 opcode 出现。

---

## 2. ISM 脚本文件格式（头部布局）

文件头 magic 为 `"ISM SCRIPT\x00\x00"`（12 字节）。头部字段全部小端：

| 偏移 | 字段 | 类型 | 说明 |
| --- | --- | --- | --- |
| `0x00` | magic | 12B | `"ISM SCRIPT\x00\x00"` |
| `0x0C` | file_size | u32 | 文件总大小 |
| `0x10` | code_base | u32 | 代码区起点，**亦等于头部结束偏移（header_end）** |
| `0x14` | count_a | u32 | 记录数 A；**不恒等于 count_d**（如 SYSTEM.ISM=1 而 count_d=0；ANIM=46 而 count_d=48）。须原样保留 |
| `0x18` | unk_18 | u32 | 恒等于 num_entries |
| `0x1C` | num_entries | u32 | 入口表条数 |
| `0x20` | entries[] | 12B/条 | 入口表，每条 `(off, p1, p2)`，off 为 code-relative |
| — | count_d | u32 | 函数表条数（.func 记录数） |
| — | count_1 | u32 | 记录数 1；**通常 = count_d，但 MAINSYS=91 而 count_d=93**（差值即具名全局变量条数）。须原样保留 |
| — | count_2 | u32 | 记录数 2（MAINSYS=2，多数为 0） |
| — | funcs[] | 变长 | 函数表，每条 `(off, p1, p2, name_len, name+NUL)`，记录大小 = name_len + 14 |
| — | unk_y0 | u32 | 恒等于 count_e |
| — | count_e | u32 | 字符串表 e 条数 |
| — | count_f | u32 | 字符串表 f 条数（本批 14 个样本全为 0） |
| — | strings_e[] | 变长 | 每条 `len + bytes + NUL`（记录大小 = len + 2） |
| — | strings_f[] | 变长 | 同上（count_f 为 0 时无） |
| — | labels[] | 变长 | 可选，`0xFF 0x01` 标记 + `count` + 每条 `(off,p1,p2,len,name+NUL)`；本批样本均无 |

**布局不变量**：`header_end == code_base`（14 个样本全部满足，反汇编器会断言校验）。

### 具名全局变量（函数表中的高位置位项）

函数表（count_d）里，**off 字段最高位（`& 0x80000000`）置位的条目不是代码偏移，而是「具名全局变量」**：

```text
off = 0x80000000 | variable_index
```

例如 MAINSYS.ISM 中 `mouthposition`（off=0x80000003）、`specialCharActive`（off=0x80000964）。这类条目**不得**映射为代码标签，应原样保留原始十六进制。`count_d - count_1` 恰等于此类条目数（MAINSYS：93−91=2）。

---

## 3. 字符串内联加密算法（op_33 STR / op_45 STRREF）

内联字符串逐字节加密：`not dl; xor dl, key`，即 **明文 = (~密文) & 0xFF ^ key**。

**密钥**：`key = (opcode 字节的文件偏移 - code_base) & 0xFF`，若等于 `0xFF` 则归零。

- **STR（0x33）**：`key = (opcode_offset - code_base) & 0xFF`
- **STRREF（0x45）**：引用代码区某 STR 的位置（code-relative 偏移 ref），`key = ref & 0xFF`（等价于目标 STR 自身的 key）

**长度编码**：长度字节 `< 0xFF` 时用 1 字节（数据从 `IP+2`），否则后接 u32 长度（数据从 `IP+6`）。

佐证（ism.dll）：
- `0x10003E10`（STR）：`mov cl,[eax+1]` 取长度；`sub al,cl` 计算 key（`al`=IP 低字节、`cl`=code_base 低字节）；`not dl; xor dl,al` 解密。
- `0x10003EE0`（STRREF）：`mov ecx,[eax+1]` 取 code-relative 引用；`add eax,ecx` 得目标 STR；`cl`=引用低字节作 key。
- 实证：SYSTEM.ISM `0x2B4` 处 STR 用 `key=0x7A` 解出 `"SYSFLAG.ISM"`。

---

## 4. Opcode 长度表

| 长度 | opcode |
| --- | --- |
| **1 字节**（定长，104 条） | 除下列外的全部 opcode |
| **2 字节** | `0xF3`（SYS_F3，u8 子码） |
| **5 字节**（u32 操作数） | `0x20 0x21 0x23 0x24 0x30 0x31 0x38 0x3A 0x3C 0x3E 0x45` |
| **6 字节**（u8 + u32） | `0x39 0x3B 0x3D 0x3F` |
| **9 字节**（双 u32） | `0x0F`（SWITCH）、`0x32`（OP_32） |
| **变长** | `0x33`（STR：长度字节 `<0xFF` 时总长 `2+len`，否则 `6+len`） |

---

## 5. 全量 Opcode 字典

完整 123 个 opcode 的定义（助记符、长度、操作数、语义、handler 佐证）见 **`opcodelist.py`**，它是汇编/反汇编的唯一实现依据。分类概览：

| 类别 | opcode | 助记符 |
| --- | --- | --- |
| 分隔/空操作 | `0x00 0x22 0x2B` | NOP |
| | `0x05 0x06` | SEP（语句分隔符） |
| | `0x09 0x34` | OP_09（同 handler 别名） |
| 算术 | `0x0B 0x0C 0x0D 0x0E 0x11 0x12 0x13 0x14 0x15 0x1C 0x1D` | SHIFT / LAND / LOR / XOR / ADD / SUB / MUL / DIV / MOD / BAND / BOR |
| 比较/逻辑 | `0x16 0x17 0x18 0x19 0x1A 0x1B 0x1E 0x1F` | EQ / LT / GT / NE / LE / GE / NOT / NEG |
| 控制流 | `0x0F` | SWITCH（range/sparse 跳转表） |
| | `0x20 0x24` | JMP（同 handler 0x10005310） |
| | `0x21` | JZ（栈顶为 0 跳转） |
| | `0x25` | RET |
| | `0x28` | CALL（间接，函数索引在栈顶） |
| | `0x10` | INVOKE（动态调用） |
| 栈/变量 | `0x23 0x38` | PUSH_VAR（u32） |
| | `0x39` | PUSH_VAR（u8+u32） |
| | `0x30 0x31` | PUSHI（立即数） |
| | `0x3A 0x3C 0x3E` | SET_VAR（u32） |
| | `0x3B 0x3D 0x3F` | SET_VAR（u8+u32） |
| | `0x32` | OP_32（双 u32） |
| 字符串 | `0x33` | STR（内联加密字符串） |
| | `0x45` | STRREF（引用代码区某 STR） |
| 字符串/比较 | `0x40 0x41 0x42 0x43 0x44 0x46 0x47` | TEST_BIT0/1/2 / STRLEN / STR_OP / OP_46 / OP_47 |
| 消息/控制 | `0x29 0x2A 0x2C 0x2D 0x2E 0x2F 0x50 0x51 0x52 0x60 0x61` | END / FRAME / MSG / LOOP / OP_2E / OP_2F / OP_50 / OP_51 / OP_52 / DISPATCH |
| 罕见 | `0x35 0x36 0x37` | OP_35 / OP_36 / OP_37（未在样本中使用） |
| 系统调用 | `0x80–0xFF` | SYS_xx（引擎内建 API，逐条调用宿主 ism.dll 函数；`0xF3` 带 u8 子码） |

> 同一助记符可能对应多个 opcode（如 JMP=`0x20`/`0x24`、PUSHI=`0x30`/`0x31`、SEP=`0x05`/`0x06`、NOP=`0x00`/`0x22`/`0x2B`）。
> 为保证**零突变 round-trip**，反汇编器对歧义助记符自动追加 `_XX` 后缀（如 `JMP_20`/`JMP_24`、`PUSHI_30`/`PUSHI_31`），汇编器据此精确还原字节。

---

## 6. 控制流与跳转基准

| 指令 | 操作数 | 基准 | 公式 |
| --- | --- | --- | --- |
| JMP `0x20/0x24` | rel32（有符号） | 指令自身偏移（opcode 字节） | `target = instr_off + rel` |
| JZ `0x21` | rel32（有符号） | 同上 | `target = instr_off + rel` |
| SWITCH `0x0F` | table_off(u32) + count(u32) | 表 default/case 相对**指令自身偏移** | `target = instr_off + rel` |
| STRREF `0x45` | ref(u32) | **code_base** | `target = code_base + ref` |
| 入口/函数/标签 off | off(u32) | **code_base** | `target = code_base + off`（高位未置位时） |

佐证（ism.dll `0x10005310` JMP）：`mov eax,[0x100640e4]; add eax,[eax+1]; mov [0x100640e4],eax` —— 即 `IP += dword[IP+1]`，基准为指令自身偏移。

---

## 7. switch 跳转表格式（关键）

SWITCH（`0x0F`）指令 9 字节：`[0x0F][table_off u32][count u32]`。`table_off` 为 **code-relative**，指向文件末尾的独立数据区；`count` 是第二操作数。

**代码区是连续的 `[code_base, code_end)`**，`code_end = code_base + min(table_off)`。所有 switch 表位于代码区之后的数据区，线性扫描必须在此停止（否则会把表字节误当 opcode）。

表首 u32 是 marker，据此分两种：

### range 表（marker `0xFF000000`）

```text
[0xFF000000][default_off i32][min u32][case_off i32 × count]
大小 = 12 + count×4
```

语义：`if value < min or value > min+count-1 → IP += default_off; else IP += case_off[value-min]`。

### sparse 表（marker `0xFE000000`）

```text
[0xFE000000][default_off i32][(key u32, off i32) × (count-1)]
大小 = count×8          ← 注意：不是 8+count×8
```

**关键**：sparse 表的「pair 0」位置被 marker/default 占用，真正的 `(key, off)` 对从下标 **1 到 count-1**，共 **count−1** 个。VM 用二分查找遍历下标 `[1, count-1]`（佐证：`0x10005417` 中 `mov ebp,1` / `lea edi,[esi-1]`，`esi`=count）。

例证：SYSLIB.ISM 的 sparse 表 count=9，共 8 对 `(key,off)`，表大小 72 = 9×8；TITLE.ISM 的 sparse 表 count=1，0 对，表大小 8 = 1×8（恒走 default）。

---

## 8. 标签化策略

- 跳转 / switch / STRREF 目标一律映射为符号标签 `loc_%08X`（文件偏移），switch 表用 `tbl_%08X`。
- 每个标签定义前插入空行；文件首个标签可例外。
- **「跳转到脚本末尾」终止惯用法**：部分脚本的入口用 JMP 跳到 `code_end`（无表时=文件尾，有表时=数据区起点），如 INITFLAG 入口 `JMP 0x1AAB`、OMAKE `JMP 0x691E`。这类目标等于 `code_end`，必须在代码段末尾为其生成标签，且标签过滤须**含 code_end**（`code_base <= o <= code_end`）。
- 变长字符串重写导致指令流长度变化时，由汇编器通过标签重定位，杜绝硬编码绝对地址。

---

## 9. 未定义 Opcode 的发现与校正流程

1. 回溯 `ism.dll` 静态表 `0x10037420`，确认该字节是否属于合法 opcode→handler 映射；
2. 检查邻近指令边界：是否因上一条指令长度误判导致连锁偏移（当前「opcode」实为操作数的一部分）；
3. 修正定义回写 `opcodelist.py`，重新全量反汇编，直至地址空间零歧义、round-trip 逐字节一致。

> 本次逆向中曾据此补齐 `0x29`（END，handler `0x10004B60`）、`0x2A`（FRAME，`0x10004C70`）、`0x2B`（NOP，`0x10004E50`）三个缺省 opcode；并纠正了 sparse 表大小公式与「代码区边界」判断，最终达成 14/14 样本 bit-perfect。

---

## 10. 零突变验证结论

对解包自 `data.isa` 的全部 14 个 `.ISM` 样本执行 `反汇编 → 汇编` 往返，SHA256 全部一致：

```text
ANIM / GAMEMAIN / INITFLAG / MAINSYS / OMAKE / STAFF / SYSFLAG /
SYSHIST / SYSLIB / SYSMENU / SYSTEM / TC_ED / TC_OP / TITLE   → 14/14 MATCH
```
