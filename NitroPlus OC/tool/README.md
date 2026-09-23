# tool/ —— 「Hello,world.」汉化工具链

引擎：NitroPlus “OC” 引擎（`HelloWorld.exe` + `system.dll`）　｜　全部结论见 `../报告_引擎分析与工具说明.md`

| 文件 | 用途 |
|---|---|
| `pak_tool.py` | 封包工具（OCArchive v3 `.pak`）：list / unpack / pack / extract / verify |
| `opcodelist.py` | `.nps` 指令集与行内记号定义（唯一真值源，含属性 schema 与转义规范） |
| `nps_disasm.py` | 反汇编：`.nps` → `.nps.asm.txt`（语义汇编，含标签/跳转/选择肢/名牌分析） |
| `nps_asm.py` | 汇编：`.nps.asm.txt` → `.nps`（零突变还原） |
| `nps_text.py` | 文本提取 / 写回（汉化核心）：`extract` / `inject` / `selftest` |

## 快速开始

```bash
# 1) 解包
python pak_tool.py unpack ../script.pak -o ../unpack/script

# 2) 反汇编（语义视图，可用于核对结构）
python nps_disasm.py ../unpack/script/script -o ../work/asm

# 3) 提取待译文本（每文件 xxx.json + xxx.json.meta.json）
python nps_text.py extract ../unpack/script/script -o ../text/script

# 4) 翻译 xxx.json 的 message 字段（必须能 CP932 编码）

# 5) 写回译文（--linebytes N 可选：按 N 字节断行并处理禁则；--name-map 统一人名）
python nps_text.py inject ../unpack/script/script ../text/script -o ../out/script --linebytes 50

# 6) 回写 pak（先备份原 pak！）
python pak_tool.py pack ../script.pak ../out/script -o ../script.new.pak
#   字体名改动在 system.pak：
python pak_tool.py pack ../system.pak ../unpack/system -o ../system.new.pak

# 7) 验证
python pak_tool.py verify ../script.pak --mem      # 应 bit-perfect
python nps_text.py selftest ../unpack/script/script # 空注入应逐字节一致
python nps_disasm.py --verify ../unpack/script/script
```

## 约定与注意

* 所有文件读写都用 **CP932**（`cp932` / `x-sjis`），不要用 UTF-8 打开 `.nps`。
* `pack` 会依据原 pak 的索引重建：条目顺序、存储方式（zlib/原样）、条目头 16 字节混淆、尾部 0x100000 全部保持一致；`zlib.compress(data, 6)` 与原始压缩逐字节相同，因此未改动即 bit-perfect。
* `.nps` 是运行时解析的文本，**没有长度/偏移约束**，译文可任意长度（方案 B）；屏幕排版用 `--linebytes` 控制。
* 不可 CP932 编码的简体字会被明确报错（含字符与位置），需先做日繁字形映射。
* 名牌显示名默认改 `<VOICE NAME="…">`；`<BUSTUP NAME="…">` 属立绘绑定，默认不动。
* 不要用调试器/FileMon/RegMon 启动游戏本体（exe 内含反调试检测）。

## 拖放支持

`.pak` 拖到 `pak_tool.py` = 解包；`.nps` 或目录拖到 `nps_disasm.py` = 反汇编；
`.nps.asm.txt` 拖到 `nps_asm.py` = 还原。

## map_refresh.py —— 按映射表刷新译文（简体 → 日繁字形）

```bash
# 在 Angel Nyanya 目录下（默认读 ./output，写 ./output_mapped）
python <项目>\tool\map_refresh.py --check           # 先体检：剩余不可编码字符
python <项目>\tool\map_refresh.py -o output_mapped   # 正式刷
python <项目>\tool\map_refresh.py output --inplace   # 就地覆盖（自动 .bak）
```
* 映射表自动查找：`scripts/subs_cn_jp.json`（CN→JP，2997 条）、`补充映射.txt`（漏网字补充）、
  或任意两列 TSV（`hanzi2kanji_table.txt` 形式）；可用 `--map` 指定、多表叠加。
* 只改 `message`（`--with-name` 加 name），**pre_jp 永不动**；长键优先、单遍替换 ⇒ **幂等**。
* 附带两步自动修复 / 体检：
  1. **标签引号修复**：译文里被写成全角引号的属性定界符（`<wait time=“5”>`）自动修回 `"`；
  2. **内嵌记号体检**：`<K>`/`<R>`/`<se>` 等被翻译弄丢或改写的条目写入 `_记号体检.tsv`。
* 输出仍带 `剩余不可编码字符` 清单（含 id 与上下文），清零后再回写游戏。

## nps_refill.py —— 增量补漏提取（旧 id 一律不变）

```bash
python tool/nps_refill.py unpack/script/script text/script -o text/script_fixed --gap text/gap
```
* 用**修复后**的提取逻辑重提，与 `text/script` 按 `(line_no, kind, pre_jp)` 配对：
  配对上的**沿用旧 id、旧内容**；新条目 id 从 `max(旧 id)+1` 顺延，排在文件末尾。
* `--gap` 输出只含新增条目的**补翻包**，格式同 `text/script/*.json`，可直接送翻译。
* 退出码非 0 = 有旧条目配对不上（会逐条列出，并原样保留，绝不静默丢内容）。

> 为什么需要它：直接重提取会让新条目按行序插入，**导致所有既有 id 后移**，
> 已翻译的 output 全部错位。本工具保证「老译文零重翻」。

## merge_gap.py —— 老译文 + 补翻包 → 可注入的完整目录

```bash
python tool/merge_gap.py <老译文目录> <gap译文目录> -o <输出目录> [--meta text/script_fixed]
```
* 以 id 为主键合并；两边 `pre_jp` 不一致直接报错（防错配）；自动附带 `.meta.json`。
* 输出目录可直接喂给 `nps_text.py inject`。

## 文本提取的判定规则（修复后）

| 行内容 | 是否提取 | 说明 |
|---|---|---|
| 含假名/汉字 | ✅ | 主体 |
| 含拉丁字母且非标签残片 | ✅ | 英文界面行，如 `now connecting....` |
| 纯标点/省略号（`「‥‥‥‥」`、`‥‥。`） | ❌ | 1,561 条，无需翻译 |
| 纯数字/编号 | ❌ | 日期、`03_200` 之类 |
| 去掉标签后为空（`<se …>` `<LAYER …>` `<clear>`） | ❌ | 纯命令行 |
| `//` 开头 | ❌ | 注释（`--with-comments` 可打开） |

**行首是否以 `<` 开头，与是否可提取无关** —— `<K>`（消息内换行）常写在续行行首。
