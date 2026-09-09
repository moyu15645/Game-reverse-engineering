# collection — 《タイムカプセル “春”》ISM 引擎汉化工具链归档

本目录是从项目工作区集中整理出来的**完整工具链归档**，包含逆向分析文档、全部工具脚本、翻译映射表和说话人标签对照表。

## 目录结构

```
collection/
├── ISM引擎逆向分析报告与工具使用说明.md   ← 主文档（引擎分析 + 工具用法 + 流水线 + 教训）
├── tools/                                  ← 核心工具（原 ism_tools/）
│   ├── opcodelist.py      123 个 opcode 完整定义
│   ├── disassembler.py    反汇编器（.ISM → asm.txt）
│   ├── assembler.py       汇编器（asm.txt → .ISM，变长重布局+重加密）
│   ├── isa_tool.py        封包/解包 data.isa（list/unpack/pack/verify）
│   ├── ism_extract.py     文本提取（对白/人名/语音 → JSON）
│   ├── translate.py       LLM 批量翻译 + 日繁映射
│   ├── ism_inject.py      译文注入（写回 .ISM）
│   ├── vm_analysis.md     VM 格式真值源文档
│   ├── README.md          原工具链简明说明
│   └── asm.txt            SYSTEM.ISM 示例反汇编输出
├── scripts/                                ← 辅助脚本（原 _work/）
│   ├── name_zh.py         说话人标签中日对照表（60+ 条）
│   └── build_name_patches.py  构建说话人标签补丁
└── data/                                   ← 依赖数据
    └── hanzi2kanji_table.txt   简体→日文汉字映射表（3018 条）
```

## 依赖关系

- `disassembler.py`、`assembler.py`、`ism_extract.py`、`ism_inject.py` 依赖同目录的 `opcodelist.py`（已用 `sys.path.insert(0, dirname(__file__))` 保证同目录 import，复制到 `tools/` 后依然成立）。
- `translate.py` 独立（仅用标准库 `urllib`），但内含翻译缓存/输出目录的相对路径。

## ⚠️ 复制后若要「直接运行」需注意的硬编码路径

这些脚本是为原项目目录结构（`data.isa`、`_text/`、`_work/` 都在项目根）设计的。归档到 `collection/tools/` 后，以下路径会失效，需自行调整：

| 文件 | 硬编码项 | 原值 | 说明 |
| --- | --- | --- | --- |
| `tools/translate.py` | `KANJI_TABLE` | `D:/Enginee/Engine/hanzi2kanji_table.txt` | 映射表绝对路径，归档里已备份一份到 `collection/data/`，改指向这里即可 |
| `tools/translate.py` | `CACHE_PATH` | `../_work/translation_cache.json` | 翻译缓存（断点续传） |
| `tools/translate.py` | `TEXT_DIR` | `../_text` | 对白 JSON 目录 |
| `tools/ism_inject.py` | `name_cache_path` | `../_work/translation_cache.json` | name 池补丁读取同一缓存 |
| `tools/ism_inject.py` | 查 `_text/<name>.json` | 相对当前工作目录 | 需在含 `_text/` 的目录下运行 |

**建议**：若要复现完整流水线，把 `collection/tools/` 当作 `ism_tools/`、`collection/scripts/` 当作 `_work/` 放回项目根目录结构，或在 `collection/` 下补齐 `data.isa`、`_text/`、`_work/` 后按主文档《ISM引擎逆向分析报告与工具使用说明.md》第五章节的流水线执行。

## 使用

详细用法见 `ISM引擎逆向分析报告与工具使用说明.md`（第四章节：工具完全使用说明；第五章节：完整汉化流水线）。

核心命令速览：

```bash
# 解包
python tools/isa_tool.py unpack data.isa _unpacked
# 提取文本
python tools/ism_extract.py _unpacked -o _text
# 翻译 + 日繁映射
python tools/translate.py
# 说话人标签补丁
python scripts/build_name_patches.py
# 注入
python tools/ism_inject.py _unpacked
# 回封
python tools/isa_tool.py pack _unpacked data_zh.isa
```
