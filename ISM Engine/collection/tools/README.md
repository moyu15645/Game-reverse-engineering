# ISM 引擎工具链使用说明

《タイムカプセル “春”》汉化工具链。游戏外层是 `data.isa` 封包，脚本为 `.ISM`（含 24 个剧情脚本，磁盘上伪装成 `.I` 扩展名）。

## 工具清单

| 文件 | 作用 |
| --- | --- |
| `isa_tool.py` | 解包 / 封包 `data.isa`（`list` / `unpack` / `pack` / `verify`） |
| `ism_extract.py` | 从 `.ISM` 提取对白文本（人名 / 台词 / 语音 → JSON） |
| `translate.py` | 用 LLM 批量翻译台词 → 简体，再做日繁映射（hanzi2kanji） |
| `ism_inject.py` | 把译文（`trans_kanji` 字段）写回 `.ISM`（变长重定位 + 重加密） |
| `disassembler.py` | `.ISM` 反汇编为语义化 asm 文本 |
| `assembler.py` | asm 文本汇编回 `.ISM`（变长重定位） |
| `opcodelist.py` | 123 个 opcode 定义（前两者的依赖） |
| `vm_analysis.md` | VM 格式真值源文档 |

Python 解释器（任选其一，推荐 managed）：
```
C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe
```

## 一、解包 data.isa

```bash
# 列出包内 806 个文件
python ism_tools/isa_tool.py list data.isa

# 解包到目录（生成 _order.txt 和 _isa_manifest.json）
python ism_tools/isa_tool.py unpack data.isa ./unpacked

# 验证解包→封包 bit-perfect（SHA256 一致）
python ism_tools/isa_tool.py verify data.isa
```

解包后 `./unpacked/` 里有 38 个 `.ISM` 脚本：
- **14 个系统脚本**（ANIM/GAMEMAIN/MAINSYS/SYSTEM/TITLE 等）—— UI 配置、脚本调度
- **24 个剧情脚本**（TC_SCN01_1 ~ TC_SCN06_4、TC_OP、TC_ED）—— **对白文本在这里**

> 剧情脚本在磁盘上的扩展名是 `.I`（如 `TC_SCN01_1.I`），这是文件名拼接规则
> 造成的表象（name[12] + r2 + r3 三段拼接），解包后已自动还原为 `.ISM`。

## 二、提取对白文本

```bash
# 提取单个脚本
python ism_tools/ism_extract.py ./unpacked/TC_SCN01_1.ISM -o out.json

# 批量提取整个目录（每个 .ISM 生成一个 .json）
python ism_tools/ism_extract.py ./unpacked -o ./text

# 输出纯文本（逐行，便于人工浏览）
python ism_tools/ism_extract.py ./unpacked/TC_SCN01_1.ISM --format txt
```

输出 JSON 结构（符合翻译规范）：

```json
{
  "id": 1,
  "name": "男の子",
  "pre_jp": "……さや、ついたよ。",
  "message": "……さや、ついたよ。",
  "voice": "M_111_001.ogg",
  "offset": "0x632"
}
```

- `name`：说话人名（旁白为空字符串）
- `pre_jp` / `message`：原文台词（翻译后填 `message`）
- `voice`：语音文件名（无语音为空）
- `offset`：台词 STR 的文件偏移（注入定位用）

**提取结果**：24 个剧情脚本共 **9635 条对白、199277 字符**，其中 59.1% 带语音。
已生成到 `_text/` 目录。

## 三、翻译（translate.py）

```bash
# 全量翻译 _text/*.json 的 message 字段（去重 + 断点续传，缓存于 _work/translation_cache.json）
python ism_tools/translate.py

# 测试少量（先翻译前 N 条唯一台词）
python ism_tools/translate.py --limit 100
```

- 模型：`deepseek-v4-flash`（中转站 https://pro.gemai.cc，OpenAI 兼容接口）。
- 流程：日文台词 → 简体中文（`trans` 字段）→ 日繁映射（`trans_kanji` 字段，Shift-JIS 可编码）。
- 日繁映射表：`D:/Enginee/Engine/hanzi2kanji_table.txt`（简体 → 日文汉字，3018 条）。
- 自动跳过纯 ASCII/省略号等无日文串（如 `START`），原样保留。
- 换行标记（字面 `\n` / `\N`）用占位符保护，翻译后逐字还原，不被模型改成真实换行。
- 模型偶发「回显不翻译 / 非法 JSON」，已内置回显检测 + 拆半重试 + 单条兜底。

## 四、注入译文（ism_inject.py）

```bash
# 注入目录下所有 .ISM（读 _text/<name>.json 的 trans_kanji，写回 _unpacked/*.ISM）
python ism_tools/ism_inject.py _unpacked
```

- 内联 STR 直接改；共享 STRREF 改其目标 STR（去重后同一原文同一译文，无冲突）。
- 复用 assembler 做变长重布局 + 按新位置重加密，未翻译部分逐字节保留（已偏移级验证）。

## 五、回封 data.isa

```bash
# 从 _unpacked/ 重新封包（变长文件自动追加到数据区末尾）
python ism_tools/isa_tool.py pack _unpacked data_zh.isa
```

## 完整流水线

```
isa_tool unpack data.isa _unpacked/       # 解包
ism_extract _unpacked -o _text/           # 提取文本 → JSON
translate.py                              # 翻译 + 日繁映射
ism_inject _unpacked                      # 注入译文 → 重建 ISM
isa_tool pack _unpacked data_zh.isa       # 回封
（游戏实测，DOSBox/目标环境验证）
```

关键约束（详见 `备忘录.txt`「ISM 引擎」章节与 `vm_analysis.md`）：
- 原文 asm → 重新汇编 ISM 必须 bit-perfect（已 14/14 验证通过）
- 变长文本不能追加到文件尾，必须重新汇编更新所有受影响偏移
- 人名通过 `op_45.strref` 引用名字池，同一个人名只回写一次
- 台词中的 `\n` / `\N` 是字面两字符（反斜杠 + n/N），翻译时需保留换行结构
