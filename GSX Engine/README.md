# The Beautiful World（Enterbrain, 2011）汉化工程

GSX 引擎逆向 + 文本提取 + AI 批量翻译 + 封包注入的完整工程。
三章（第一章 / 第二章 / 最终章）**成品位于 `release/`**。

---

## 一分钟上手

```bash
# 1) 只做一章（解包 → 提取 → 翻译 → 后处理 → 注入 → 重新封包 → 反汇编校验）
python tool/run_chapter.py 1        # 或 2 / 3

# 2) 零突变自检
python tool/verify_roundtrip.py                       # 反汇编↔汇编 509/509
python tool/fpack.py verify "Beautiful World 1.1/filepack.bin"   # 封包 BIT-PERFECT

# 3) 成品自检
python tool/verify_release.py "release/Beautiful World 1.1" \
    --meta work/ch1/text/jp.json.meta.json --zh work/ch1/text/zh.json \
    --orig "Beautiful World 1.1"

# 4) 两层自检
python tool/check_font_slots.py                      # 显示层：多余字形 0 / 残留假名 3 / 非 CP932 0
python tool/check_message_syntax.py                  # 字节层：0x19 前缀 / 裸 ASCII 指令，0 条不合规
```

**成品用法**：把 `release/Beautiful World 1.x/` 里的内容覆盖回游戏目录（或直接以该
目录为游戏目录运行 `Beautiful World *.exe`）。

> 若事后修正了映射表（`trad2simp.txt` / `t2s_extra.txt` / `names_map.py`），
> 重新跑一遍 `tool/finalize.py` 即可 —— `cache.json` 保存的是「模型原文 + 长度修复结果」，
> 是 `zh.json` 的前像，重放不丢任何数据，也不需要再调用 API。
> ⚠️ 不要把映射再作用一遍已经映射过的 `zh.json`（会二次套用槽位表，把
> `凜`(显示「你」) 再映射成另一个槽位）。

---

## 目录结构

```
Beautiful World/
├─ Beautiful World 1.1/         原始第一章（只读，未改动）
├─ Beautiful World 1.2/         原始第二章（只读）
├─ Beautiful World 1.3/         原始最终章（只读）
├─ hanzi2kanji_table.txt        字位映射表（只读，未做任何修改）
├─ docs/
│   ├─ vm_analysis.md           ★ VM 分析定义文档（唯一真值源）
│   └─ engine_report.md         ★ 引擎分析报告 + 工具使用说明
├─ tool/                        ★ 全部工具（Python 3.9+，零第三方依赖）
├─ work/                        工作区（解包 / 反汇编 / 文本 / 构建产物 / 日志）
└─ release/                     ★ 三章汉化成品
```

工具清单见 `docs/engine_report.md` 第 3、4 节。

---

## 核心事实（详见 `docs/`）

| 项目 | 值 |
| --- | --- |
| 封包 | `filepack.bin`，No-FilePlus（`Dm-No-FilePlusD`），两种形态 |
| 剧本容器 | `/Data/script/CoD.cpt`，差分替换流密码 + 自研容器 |
| VM | GSX，32 位字流：`低16位=操作码，高16位=内联参数`，变长操作数 |
| 操作码 | 全量 255 条已恢复并命名，arity 全部求解 |
| 文本 | 脚本块尾部 NUL 结尾字符串池，代码以绝对字节偏移引用 |
| 注入 | 方案 A（固定偏移原地替换），池长与全部偏移不变 |
| 编码 | 简体中文 → **繁体归一** → `hanzi2kanji_table.txt` 槽位映射 → CP932 |
| 字体表语义 | `K→V` = **写出 V，屏幕显示 K 的字形**（槽位分配表，非字形对照表） |

**文本量**：第一 / 二 / 最终章分别 3 153 / 12 533 / 12 521 条，合计 **28 207 条**；
实际改写的条目为 3 097 / 12 444 / 12 431 条（其余为角色名等按约定保持原样）。

---

## 验收状态

| 检查 | 结果 |
| --- | --- |
| CoD 解密→加密 逐字节还原 | 三章通过 |
| 反汇编→汇编 逐字节一致 | **509 / 509** |
| filepack 解包→封包 | 三章 **BIT-PERFECT** |
| 译文按原始偏移回读 | 三章 **28 207 / 28 207** 条一致 |
| 未翻译池串保持原样 | 8 448 条，差异 0 |
| 代码区改动 | 无 |
| 超预算（会被截断）条目 | 0 |
| 屏幕多余字形（模拟渲染比对） | 0 |
| 消息字节语法（28 238 条） | 0 条不合规 |
| 残留假名条目 | 3（均为「嘴巴保持『ひ』的口型」这类必须保留处） |
| 人物名残留假名 | 0（`らいん→莱茵`、`セカンド→赛肯德`、`ひぃちゃん→小希` …） |

---

## 常见误解：`hanzi2kanji_table.txt` 怎么用

这张表**不是**「简体→日文字形对照表」，而是**字体槽位分配表**：

```
K <TAB> V   ⇒   在游戏里写出 V，屏幕上显示 K 的字形
```

因此 **不能** 只做「查表替换」——模型输出里夹带的繁体/日文字形
（`來 會 個 點 說 櫻 遊 沒` …）里有一批恰好是别人家的槽位
（`礌→來`、`苁→會`、`鲟→個`、`爸→點`、`吡→遊`、`伥→櫻`），
直接落盘就会把 `櫻坂` 显示成 `伥坂`（这就是玩家看到的那类错字）。

正确顺序是：**先繁体归一到简体（`tool/trad2simp.txt`）→ 再查槽位表**。
`tool/check_font_slots.py` 可离线审计成品是否还有漏网的槽位冲突。

---

## 已知限制

1. EXE 内约 30 条界面/错误提示文字（`セーブ`、`ロード`、`音量`、`画面`、`既読` …）
   未汉化——它们硬编码在 `.rdata`，需等长原地改写，本工程为降低崩溃风险未做。
2. 译文按用户约定的**日繁字形码位**写入，需配合用户自制 JIS 映射字体才能显示简体字形。
   个别字在该字体里没有可用槽位（如「篠/笹」原名 ⇒ 写作「筱/竹」），
   已在 `tool/t2s_extra.txt` 中用译名替换解决。
3. 注音（ruby）标记按中文习惯删除，仅保留基文。
4. 方案 A 下每条文本有固定字节预算；超预算条目经「确定性压缩 + 模型压缩」两轮修复，
   仍超长者按字符边界截断（构建日志会逐条列出）。
5. 极少数过场/邮件里的 ASCII 词组（`flyingchick`、`SoC2`、邮箱地址）按原文保留，
   未做本地化改写。
6. **消息文本有严格的字节级规矩**（见 `docs/engine_report.md` §5.3）：每个单字节字符
   必须写成 `0x19 + 字节`，且不能残留孤立 `0x19`——否则引擎的消息解析器会错位，
   最终把 ASCII 当成面部件指令执行并触发 `isSetupFacePose` 断言。本工程的重建流程
   已内置 `check_message_syntax.py` 兜底。

