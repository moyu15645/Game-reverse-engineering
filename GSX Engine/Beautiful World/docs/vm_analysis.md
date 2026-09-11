# `vm_analysis.md` — GSX 脚本虚拟机分析定义文档

> 目标作品：**The Beautiful World（Enterbrain / 2011）**
> 第一章 `Beautiful World １章.exe`、第二章 `Beautiful World ２章.exe`、
> 最终章 `Beautiful World 最終章.exe`
>
> 本文档是后续所有解析、重建工作的**唯一真值源**。每一条结论都给出证据
> （可执行文件偏移 / 反编译代码 / 字节对照），未经证据支持的推断均标注
> 「推断」。

---

## 0. 工程总览

| 项目 | 结论 |
| --- | --- |
| 宿主可执行文件 | `Beautiful World <章>.exe`（32 位 PE，ImageBase `0x00400000`） |
| 脚本 VM | **GSX VM**，源码文件 `.\src\system\script\GsxVm*.cpp` |
| 用户界面/系统层 | `sys_ui_*`、`sys_sc*`、`ComGame*` 等模块（EXE 内） |
| 资源封包 | `filepack.bin`（"No-FilePlus" 格式，`SysFilePack.cpp` / `SysFPlus.cpp`） |
| 剧本容器 | `/Data/script/CoD.cpt`（流加密容器，`FileLoad.cpp`） |
| 图形包 | `/Data/script/script2d.bin`、`title_menu.bin`（索引式包） |
| 文本编码 | Shift-JIS / CP932 |
| 文本存储位置 | **脚本块的"字符串池"段**（见 §4） |
| 文本引用方式 | 代码流中以 **池内绝对字节偏移** 引用 |
| 汉化注入方案 | **方案 A：固定偏移原地替换**（见 §7） |

EXE 内可见的源码路径字符串（`list_strings` / VA）：

```
004c89c8  .\src\system\core\windows\SysFilePack.cpp
004c8fxx  .\src\system\core\common\SysFPlus.cpp
004cc820  .\src\system\script\FileLoad.cpp
004cc860  .\src\system\script\GsxVm.cpp
004cc878  .\src\system\script\GsxVm_MsgProcs.cpp
004cccxx  .\src\system\script\GsxVm_GraphicsProcs.cpp
004ccdxx  .\src\system\script\GsxVm_SaveProcs.cpp / _SenProcs / _SoundProcs / _SpecialProcs / _Util
```

---

## 1. 资源封包 `filepack.bin`（No-FilePlus）

### 1.1 魔数

包**尾**固定 16 字节 `"Dm-No-FilePlusD\0"`；但顶层包的**首 4 字节是文件长度**，
因此顶层包靠路径名打开，而包内嵌套包靠 `strncmp(ptr,"Dm-No-FilePlusD",15)` 识别：

```c
// VA 0x0042f1c0
bool FUN_0042f1c0(char *p){ return _strncmp(p,"Dm-No-FilePlusD",0xf)==0; }
```

### 1.2 两种容器形态

**格式 A —— 带名字表（`filepack.bin` 顶层包）**

```
0x00 u32 total_size        文件总长度
0x04 u32 count             条目数
0x08 u32 meta_size         == 数据区起点（=0x20 + count*12 + 名字表长度，16 字节对齐）
0x0C u32 unk0c             恒为 4（保留）
0x10 u32 unk10             恒为 16（保留）
0x14 u32 rec_off           记录表偏移（恒为 0x20）
0x18 u32 names_off         名字表偏移（= 0x20 + count*12）
0x1C u32 data_off          首个数据块偏移（== meta_size）
0x20 ...                   记录表 count × {u32 name_off; u32 data_off; u32 size}
names_off ...              名字区：每个名字以 0x00 结尾，整体按 4 字节对齐
data_off ...               数据块：依次排列，每块起始按 16 字节对齐
```

证据（VA `0x0040b470`，`SysFilePack` 打开函数）：
读取文件前 `0x14` 字节，取**偏移 `0x08`** 的 u32 作为元数据长度，
分配并读入该长度，然后调用 `FUN_0040b3c0` 解析：

```c
// VA 0x0040b3c0
*(int*)(pack+0x14) = pack + *(int*)(pack+0x14);   // 记录表偏移 -> 指针
*(int*)(pack+0x18) = pack + *(int*)(pack+0x18);   // 名字区偏移 -> 指针
*(int*)(pack+0x1c) = pack + *(int*)(pack+0x1c);   // 数据区偏移 -> 指针
for (i=0;i<count;i++) records[i].name_off += pack;   // 名字偏移 -> 指针
```

名字区步长实测：`stride = round_up(len+1, 4)`。

**格式 B —— 纯索引（`script2d.bin` / `title_menu.bin` / `vpaku.bin` / `se_com.bin`）**

```
0x00 u32 total_size
0x04 u32 count
0x08 u32 rel[count]        每个条目数据的绝对文件偏移
align16(8+count*4) ...     数据块（第 i 条 = rel[i] .. rel[i+1]）
```

VM 侧的读取接口（`SysFPlus.cpp`）：

```c
// VA 0x0042f040
int FUN_0042f040(int fp,int no){ return fp + *(int*)(fp + 8 + no*4); }   // 条目数据指针
// VA 0x0042f0f0  条目长度 = 下一偏移 - 本偏移
```

### 1.3 实测数据

| 章 | filepack.bin 大小 | 条目数 | 名字表偏移 | 数据区偏移 |
| --- | --- | --- | --- | --- |
| 1 | 328 142 656 | 508 | 6 128 | 20 080 |
| 2 | 417 669 696 | 649 | 7 820 | 25 696 |
| 3 | 426 922 720 | 692 | 8 336 | 27 520 |

`fpack.py verify`：**三章均为 BIT-PERFECT**（解包→封包逐字节一致）。

---

## 2. 剧本容器 `/Data/script/CoD.cpt`

### 2.1 外层流加密

`CoD.cpt` 全文被一层**差分替换密码**混淆（不是压缩）：

```c
// VA 0x00466800 —— 原地解密（对称）
void FUN_00466800(uint8 *p, int n){
  uint8 prev = 0;
  for (int i=0;i<n;i++){ uint8 b=p[i];
      p[i] = TABLE[(uint8)(p[i] - prev)];
      prev = b; }
}
// 调用点 0x00466854: MOV AL, byte ptr [EAX + 0x774728]   ← 表地址立即数
```

* 表为 **256 字节置换表**（`len(set(tbl))==256`）。
* 表地址由 `MOV AL, byte ptr [EAX+imm32]`（机器码 `8A 80 xx xx xx 00`）定位：

| 章 | 表 VA | 表文件偏移 |
| --- | --- | --- |
| 1 | `0x00774728` | `0x372528` |
| 2 | `0x007746F8` | `0x3724F8` |
| 3 | `0x007746F8` | `0x3724F8` |

* 解密后首 4 字节为魔数 `00 04 05 01`（LE u32 `0x01050400`），
  紧接着 u32 为脚本数。校验代码：

```c
// VA 0x00457c60
uVar1 = FUN_00408e30("script/CoD.cpt", &size);  // 读入内存
FUN_00457b60(uVar1, 1, size);                   // 解密
FUN_00457c20(&magic, 4);
if (magic == 0x01050400) FUN_00457d50();        // 解析
else /* 「シナリオバージョンが違います。」 */;
```

### 2.2 容器结构

```
0x000   u32 magic = 0x01050400
0x004   u32 script_count
0x008   0xF8 字节填充（全 0）
0x100   index[script_count]，每项 16 字节：
            u32 cum_off      (累计载荷偏移)
            u32 payload_size (= code_words*4 + data_size)
            u32 0
            u32 0
0x100+count*0x10
        脚本块，第 i 块的偏移 = 0x100 + count*0x10 + i*0x40 + cum_off[i]
        其中 cum_off[i] = Σ_{j<i} payload_size[j]
        每个脚本块：
            0x00 u32 code_words     代码区 32 位字数
            0x04 u32 data_size      字符串池字节数
            0x08 8 字节保留
            0x10 char name[16]      脚本名，如 "01_001"
            0x20 16 字节保留
            0x30 16 字节保留
            0x40 ...                code  : code_words*4 字节
            ...                     data  : data_size 字节
```

证据：`FileLoad.cpp` 的解析函数 **VA `0x00457d50`**：

```c
read4(&count, 4);
seek(+0xF8);                                  // 到 0x100
for (i=0;i<count;i++){
    read4(&rec[i].f12, 4);                    // cum_off
    read4(&rec[i].f16, 4);                    // payload_size
    rec[i].f12 = i*0x40 + count*0x10 + 0x100 + rec[i].f12;
    skip(8);
}
for (i=0;i<count;i++){
    seek(rec[i].f12);                         // 块起始
    read4(&rec[i].f10, 4);                    // code_words
    read4(&rec[i].f14, 4);                    // data_size
    skip(8);
    read(&rec[i].name, 0x10);
    read(&rec[i].resv,  0x10);
    skip(0x10);
    FUN_00466660(rec, f10);                   // GlobalAlloc(f10*4)
    read(rec[i].code_buf, f10*4);             // 代码区
    FUN_004666b0(rec, f14);                   // GlobalAlloc(f14)
    read(rec[i].data_buf, f14);               // 字符串池
}
```

`ScriptClass` 内存布局（在内存中每条记录 0x160 字节）：

| 偏移 | 含义 |
| --- | --- |
| `+0x24` | code 缓冲区指针 |
| `+0x28` | code_words |
| `+0x34` | data 缓冲区指针 |
| `+0x38` | data_size |
| `+0x2C` | 每脚本保存的 PC（`DAT_00ed2eb8 + 0x2c + idx*0x160`） |
| `+0x50` | 脚本名（16 字节） |
| `+0x18 / +0x1C` | 等待计数器 / 输入标志（`FUN_00437800`） |

### 2.3 实测数据

| 章 | CoD.cpt 大小 | 脚本数 | 脚本名示例 |
| --- | --- | --- | --- |
| 1 | 411 941 | 106 | `01_001`…`01_033`、`cg_01`…`cg_45`、`exec_*`、`init_*`、`subfunc_*`、`title_main`、`topmenu` |
| 2 | 1 478 354 | 201 | `01_*`…`05_*`、`cg_*`… |
| 3 | 1 487 505 | 202 | `01_*`…`05_*`、`cg_*`… |

---

## 3. GSX 虚拟机

### 3.1 体系结构

* **单字节流 → 32 位字流**：代码区是 `u32` 数组，但**每个字同时编码操作码与内联参数**。
* 分发（**解释器主循环**，`GsxVm.cpp`，VA `0x00437800`）：

```c
uVar1 = FUN_00466700();                          // 取下 1 个 32 位字
uVar1 = words[PC++];                             // 等价实现
op    = uVar1 & 0xFFFF;                          // 低 16 位：操作码
inline= uVar1 >> 16;                             // 高 16 位：内联参数
handler = handler_table[op];                     // DAT_00ed2ec0
if (handler == 0) break;                         // 未定义操作码：VM 停机
if (handler(inline) != 2) continue;              // 返回 2 表示让出（等待输入/定时）
```

* **操作数**：操作码之后紧跟若干 32 位字，由 handler 自己通过 `FUN_00466700()`
  从同一字流中取用，因此**指令变长**。

```c
// VA 0x00466700
if (rec[0] == 0) return 0x80000000;              // 脚本未运行
if (rec[0x0B] < rec[0x0A]) { rec[0x0B]++; return *(u32*)(rec[9] + (rec[0x0B]-1)*4); }
else return 0x80000000;                          // 取指越界
```
`rec[9]` = code 缓冲区，`rec[0x0A]` = code_words，`rec[0x0B]` = PC。

### 3.2 操作码分发表

* 表基址 **`DAT_00ed2ec0`**，容量 1024 项（`FUN_00459520` 中 `for(i<0x400) tbl[i]=0`）。
* 初始化函数 **VA `0x00459520`**，形如 `MOV dword ptr [0x00ed2Xxx], 0x0046XXXX`。
  直接从机器码扫描 `C7 05 <addr32> <imm32>` 即可恢复完整映射。
* 本作实测 **255 个已定义操作码**（编号 0…308，中间有空洞）。

```python
# 提取片段
for m in re.finditer(rb'\xc7\x05', code):
    addr = u32(code, off+2); imm = u32(code, off+6)
    if 0xed2ec0 <= addr <= 0xed3ec0:
        opcode = (addr-0xed2ec0)//4;  handler = imm
```

### 3.3 操作数元数（arity）与指令边界

操作数个数 = 该 handler **实际执行**的 `FUN_00466700` 调用次数。求法：

1. 统计每个函数的 `call 0x00466700` 直接调用点数量（启发式初值）；
2. 用约束求解器 `tool/solve_arity.py` 修正：要求**全部 509 个脚本**
   （三章合计）按该 arity 表切分后**恰好消耗 `code_words` 个字**。

实测结果：**509/509 脚本完全可精确切分**，`arity` 取值 0…12。

### 3.4 虚拟栈

```c
// VA 0x00458330  —— PUSH
*(u32*)(vm + 0x24 + sp*4) = type;          // 类型栈
*(u32*)(vm + 0x424 + sp*4) = value;        // 值栈（type==2 时拷贝字符串）
sp++;
// VA 0x00458430  —— POP
sp--;  *out_type = *(u32*)(vm + 0x24 + sp*4);
// VA 0x00458f00  —— RETURN（弹出 脚本号 + PC）
```

栈上限 0xFE（断言 `0 < StackPointer`、`CALLスタックがオーバーしました。`）。

### 3.5 关键操作码（已反编译确认）

| op | handler | 源码模块 | arity | 语义 |
| --- | --- | --- | --- | --- |
| 0 | `0x00460530` | GsxVm | 0 | NOP（49 个操作码共用此空实现） |
| 82 | `0x00462a90` | GsxVm_GraphicsProcs | 4 | 精灵/图片显示 `(id,x,y,?)`→`FUN_00438cc0` |
| 114 | `0x00460700`→`0x00460620` | GsxVm_MsgProcs | 1 | **显示台词**：取 1 个操作数=池内偏移，`FUN_00471960(data_base+off)` |
| 120 | `0x00458f00` | GsxVm | 0 | RETURN |
| 250 | `0x00460d70` | GsxVm_MsgProcs | 1 | 文本分页/等待 |
| 290 | `0x00460580` | GsxVm_MsgProcs | 1 | 消息模式（参数 0…9，其余断言失败） |
| 296 | `0x004607f0` | GsxVm_MsgProcs | 1 | 设置消息 ID / 语音号（`FUN_0049c5d0`） |
| 251 | — | GsxVm_GraphicsProcs | 1 | 图形刷新（占位 `0x00460530` 的 568 个样本修正） |

> 完整的 255 条表见 `tool/opcodelist.py`。

---

## 4. 字符串池与文本编码

### 4.1 池结构

字符串池是一段**连续的 NUL 结尾字符串序列**，无独立索引：

```
str_0 \0 str_1 \0 str_2 \0 ... str_{n-1} \0
```

代码区通过**绝对字节偏移**引用（op 114 的操作数即偏移值）。

### 4.2 文本内控制码

| 字节 | 含义 | 出现次数（第 1 章） |
| --- | --- | --- |
| `0x0C` | 消息结束标记（每个台词串结尾必有） | 全部 |
| `0x0D` | 换行 / 翻页 | 47（串内） |
| `0x0E` | 另一种消息结尾（页末） | 部分串尾 |
| `0x0F` | 段内分段符 | 1 334 |
| `0x05 … 0x06 … 0x07` | **注音（ruby）标记**：`base` `reading` | 1 407 / 1 389 / 1 389 |
| `0x19 X` | **半角字符前缀**：后随 1 个 ASCII 字符，如 `\x191\x194日` = "14日" | 906 |

汉化处理：
* `0x05/0x06/0x07` 连读音一起删除，仅保留基文（中文不需要注音）；
* `0x0D → [[BR]]`、`0x0F → [[SP]]` 作为占位符交给翻译模型，注入时还原；
* `0x19` 前缀在提取时丢弃、注入时对每个半角 ASCII 字符自动补回（长度中性）。

### 4.3 说话人名字表

`init_app` 脚本的池中有一段连续短字符串，以字面量 `NULL` 分隔，即**角色名表**
（三章均为 57 条）：

```
敷縞, らいん, 小鳥遊, 鴇乃, 如月, 陽々子, 羽野真, 詩歌, 薪叢, 来未, 東雲, 瑛,
雪平, 由羽奈, 櫻坂, 総, 鳴神, 玲, 隆司, 告春, 鹿毛川, 五日, 藤水, 柚憂, 貴志多,
央希, 霧谷, 陽南子, ？？？, 少女, 少年, 隆逸, ＊＊＊＊, 篠笹舞衣, 靖行,
女子大生１/２/３, 女子大生たち, 声, 皆井, 鑑識課, 学生A…D, 野洲長, 女性店員,
男子学生, 女性, 校内放送, 男子生徒たち, 女学生, 玲子, サッカー部員
```

---

## 5. 编码与字位约定（本工程）

* 引擎文本编码 = **CP932**；译文为简体中文，经
  `hanzi2kanji_table.txt`（3 018 条）映射为**日繁字形码位**后写入。
* 该表是「简体字形 → 特殊 JIS 码位」的**字形替换表**（配合用户自制字体
  枚举显示），因此条目形如 `说→説`、`你→凜`、`吧→槇`。
  **本工程严格按表映射，不修改任何一条既有映射。**
* 表中未涉及的字符：保持原字（不替换）。
* 若某字符既不在表中、又无法通过 `cp932` 编码：按用户要求**直接删除**
  （仅涉及 `♪` 一类符号与少量无 JIS 对应字的繁体字）。
* 为此额外提供 **`tool/jis_supplement.txt`**（27 条，后置、`setdefault` 语义、
  绝不覆盖主表），把少量模型偶发输出的繁体字映射到语义等价的日文汉字
  （如 `說→説`、`絕→絶`、`謊→嘘`、`夠→足`），避免整字丢失。

---

## 6. 地址基准汇总

| 语义 | 基准 |
| --- | --- |
| op 114 / 池字符串操作数 | **字符串池起始**（`data_base + off`） |
| 脚本块索引 `cum_off` | 块区起点 `0x100 + count*0x10` |
| filepack 格式 A 的 `name_off` / `data_off` | 文件起点 |
| filepack 格式 B 的 `rel[]` | 文件起点 |
| VM 跳转/调用 | 无（本 VM 不使用跳转；`CALL/RETURN` 通过脚本号 + PC 栈实现） |

> 与常见 galgame 引擎不同，**GSX VM 没有相对跳转指令**：
> `CALLMES`（op 7）/ `RETURN`（op 120）通过压入「脚本编号 + PC」实现跨脚本调用，
> 因此**字符串池长度变化不会破坏代码流的任何绝对跳转**——这是本工程能安全
> 使用方案 A 的关键前提。

---

## 7. 注入方案（方案 A：固定偏移原地替换）

因为 op 114 用**绝对字节偏移**定位文本，池布局必须保持：

1. 逐条把译文写回其**原始偏移**；
2. 译文之后补 `0x00` 终止符，其后到下一个字符串起点之间的空隙用 `0x00` 填充
   （该区间永远不会被读取）；
3. 因此约束是 `[前缀控制码] + 译文 + [后缀控制码] + 0x00 ≤ slot`
   （`slot = 原串长度 + 1`），预算逐条记录在 `*.meta.json` 的 `budget` 字段；
4. 超预算条目：`--force` 时按 CP932 字符边界截断（尾随控制码保留），并写入日志；
5. 池长度、块偏移、索引值、容器长度**全部不变**，加密后 CoD.cpt 与原文等长。

---

## 8. 零突变验收

```
disassembler -> assembler   509 / 509 脚本逐字节一致（三章合计）
filepack unpack -> pack     三章均 BIT-PERFECT
CoD decrypt -> encrypt      三章均逐字节还原
```

命令：
```bash
python tool/verify_roundtrip.py
python tool/fpack.py verify "Beautiful World 1.1/filepack.bin"
```
