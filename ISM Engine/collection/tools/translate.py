# -*- coding: utf-8 -*-
"""
translate.py — 台词翻译 + 日繁回写（《タイムカプセル “春”》）

流程：
    1. 读取 _text/*.json 的全部 message（日文原文），去重。
    2. 用 deepseek-v4-flash 批量翻译成简体中文（断点续传，缓存到
       _work/translation_cache.json）。
    3. 用 hanzi2kanji_table.txt 把简体逐字映射为「日文汉字」（Shift-JIS 可编码），
       得到 trans_kanji 字段。
    4. 把 trans（简体）与 trans_kanji（日繁）写回 _text/*.json。

用法：
    python translate.py [--limit N] [--batch 20] [--model deepseek-v4-flash]

依赖：无第三方库（urllib 直连 OpenAI 兼容接口）。
"""

import os
import re
import sys
import json
import time
import glob
import urllib.request
import urllib.error

API_BASE = "https://pro.gemai.cc"
API_KEY = "sk-OUY9YqTKlgjf4G4pplkWRxPxeSjRUC9RNEBYip1s5MbgXpEK"
MODEL = "deepseek-v4-flash"

# hanzi2kanji 映射表（日繁回封用）
KANJI_TABLE = "D:/Enginee/Engine/hanzi2kanji_table.txt"

# 缓存与输出目录
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "_work", "translation_cache.json")
TEXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_text")

# 人名表（保证译名一致；可自行增改）
NAME_GLOSSARY = {
    "さや": "小夜",
    "なつみ": "夏美",
    "あや": "彩",
    "ユキ": "雪",
    "亮": "亮",
    "優": "优",
    "ゆー": "优",
    "恭": "恭",
    "真帆": "真帆",
    "麻夜": "麻夜",
    "マヨ": "麻夜",
    "アキ": "阿基",
    "今日子": "今日子",
    "夏目": "夏目",
    "蔵本": "藏本",
    "村井": "村井",
    "桃井": "桃井",
    "桜井": "樱井",
    "榎本": "榎本",
    "澤村": "泽村",
    "都築": "都筑",
    "喜八": "喜八",
    "ぺロ": "佩罗",
    "ペロ": "佩罗",
    "おばあちゃん": "奶奶",
    "先生": "老师",
}

# 换行标记（字面 \\n 与 \\N，均为游戏引擎的换行控制符，必须逐字保留）
TOK_NL = "\u27e8换行\u27e9"   # 占位：字面 \n
TOK_NN = "\u27e8换行2\u27e9"  # 占位：字面 \N


def plain(s):
    """去掉换行标记，返回纯文本。"""
    return MARKER_RE.sub("", s)


# 短语气词/感叹词词典（确定性兜底）。模型对「え？」「うん」这类极短句会原样回显，
# 用词典在 LLM 之前直接命中，避免反复回显拖垮批次。
INTERJECTION_DICT = {
    # 疑问/惊讶
    "え？": "诶？", "え……？": "诶……？", "……え？": "……诶？", "え？え？": "诶？诶？",
    "え": "诶", "え……": "诶……", "ええ": "哎", "ええ？": "哎？",
    "えー": "哎", "えー……": "哎……", "えー？": "哎？", "えーと……": "那个……",
    "えっと": "那个", "えーと": "那个",
    "へ？": "嘿？", "へ……？": "嘿……？", "へえ": "哦", "へえ……": "哦……",
    "へえー……": "哦——……", "へぇ……": "哦……",
    "は？": "哈？", "はあ？": "哈啊？",
    "ん？": "嗯？", "ん……？": "嗯……？", "……ん？": "……嗯？", "ん……": "嗯……",
    "なに？": "什么？", "なに……": "什么……",
    "ほんと？": "真的？", "ほんとに？": "真的吗？",
    "うそ": "骗人", "うそ……": "骗人……", "まさか": "不会吧", "まさか。": "不会吧。",
    "……まさか。": "……不会吧。",
    # 肯定/应答
    "うん": "嗯", "うん。": "嗯。", "うん……": "嗯……", "うん……。": "嗯……。",
    "う、うん": "嗯、嗯", "う、うん……": "嗯、嗯……", "う、うん。": "嗯、嗯。",
    "うん、うん": "嗯、嗯", "……うん。": "……嗯。",
    "はい": "是", "はい。": "是。", "は、はい": "是、是", "はいっ": "是",
    "は、はいっ": "是、是", "は、はい。": "是、是。", "あ……はい。": "啊……是。",
    "……はい。": "……是。", "……はい？": "……是？",
    "ああ": "啊啊", "ああ……": "啊啊……", "……ああ。": "……啊啊。",
    "そう": "这样啊", "そうか": "这样啊", "そっか": "是吗", "そっか。": "是吗。",
    "そっかぁ……": "是吗……", "そうなんだ": "这样啊",
    "なるほど": "原来如此", "ふむ": "嗯", "ふむ。": "嗯。",
    "うーん": "嗯——", "う～ん": "嗯——", "うーん……": "嗯——……",
    # 否定/迟疑
    "いや": "不", "いや……": "不……", "いやいや": "不不", "ううん": "嗯（否认）",
    "ちがう": "不对",
    # 招呼
    "おい": "喂", "おい。": "喂。", "もしもし": "喂喂", "もしもし……": "喂喂……",
    "ねぇ": "呐", "ね、ねぇ？": "呐、呐？", "やあ": "哟",
    # 叹气/感叹
    "あ": "啊", "あ……": "啊……", "あっ": "啊", "あっ。": "啊。", "あっ……": "啊……",
    "わっ": "哇", "わっ。": "哇。",
    "ふん": "哼", "ふん……": "哼……", "ふふ": "呵呵", "ふふ……": "呵呵……",
    "ふうん……": "哼嗯……", "ふぅん……": "哼嗯……", "ふぅ……": "呼……",
    "はぁ": "哈啊", "ハァ": "哈啊", "ハァ……": "哈啊……", "ハァ…………": "哈啊…………",
    "は、はぁ……": "哈、哈啊……", "は、はあ……": "哈、哈啊……", "……ハァ。": "……哈啊。",
    "くっ……": "唔……", "くっ…………": "唔…………", "む": "唔", "む……": "唔……",
    "むう……": "唔……", "う": "唔", "う……": "唔……", "うぅ……": "唔……",
    "ふぅ": "呼", "……っ！": "……啊！", "……っ！？": "……啊！？",
    "……っとと。": "……哎哟。",
    # 连接/承接
    "そして": "然后", "そして……": "然后……", "そして、": "然后、",
    "って……": "那个……", "それが……": "那个……", "それは……": "那个……",
    "あの……": "那个……", "その……": "那个……",
    "ここは……": "这里是……", "あれは……": "那是……",
    "まあ": "嘛", "まあいい": "算了", "まあいい。": "算了。", "ちょっと": "等一下",
    "だいたいな。": "大概吧。", "さやは？": "小夜呢？",
    # 拟声
    "クイクイッ。": "（轻轻扯了扯。）", "ふつう……": "普通……",
}

# 补充常见变体（短语气词千变万化，逐个收录高频变体）
INTERJECTION_DICT.update({
    "は、はいっ。": "是、是。", "うん！": "嗯！", "えっと……": "那个……",
    "あ、うん。": "啊、嗯。", "あ、はい。": "啊、是。", "……ここか。": "……是这里吗。",
    "へええ……": "哦哦……", "……あ。": "……啊。", "あ、あの……": "啊、那个……",
    "ええ。": "哎。", "そうか……": "这样啊……", "そうだな。": "是啊。",
    "そうだな": "是啊", "そうですね": "是啊", "そうですか": "是这样吗",
    "ふぅ…………": "呼…………", "……はぁ？": "……哈啊？", "ふう……": "呼……",
    "ん…………": "嗯…………", "……そっか。": "……是吗。", "う～ん……": "嗯——……",
    "と……": "那么……", "それに……": "而且……", "……えへへ。": "……诶嘿嘿。",
    "あれ……？": "咦……？", "あははーっ！": "啊哈哈——！", "え…………": "诶…………",
    "そうですー。": "是哦。", "はいー。": "是——。", "……ま……": "……嘛……",
    "……まじで？": "……真的吗？", "マジですー。": "真的哦。", "（ん……？）": "（嗯……？）",
    "あ。": "啊。", "は……？": "哈……？", "なんで？": "为什么？",
    "こんにちは！": "你好！", "はあ……": "哈啊……", "よろしくね。": "请多关照。",
    "あ、はい……": "啊、是……", "あ、アキ……": "啊、阿基……", "あ、さや……": "啊、小夜……",
    "ごめん": "抱歉", "ごめん。": "抱歉。", "ごめん……": "抱歉……", "ありがとう": "谢谢",
    "ありがとう。": "谢谢。", "おいで": "过来", "おやすみ": "晚安", "ただいま": "我回来了",
    "おかえり": "欢迎回来", "いってきます": "我出门了", "いただきます": "我开动了",
    "かわいい": "好可爱", "すごい": "好厉害", "すごい……": "好厉害……",
    "やめて": "住手", "やめて。": "住手。", "まって": "等等", "まって。": "等等。",
    "どうして": "为什么", "どうして……": "为什么……", "なぜ": "为什么",
    "いつ？": "什么时候？", "どこ？": "哪里？", "だれ？": "谁？", "誰？": "谁？",
    "エヘヘヘ": "诶嘿嘿嘿", "エヘへへ": "诶嘿嘿", "エヘヘ": "诶嘿嘿",
    "エヘへ": "诶嘿", "エヘ": "诶嘿", "ふふ": "呵呵",
})

# 规范化：去首尾标点与重复符号，用于短语气词二次命中
def _interj_key(s):
    s = plain(s).strip()
    s = re.sub(r"[。、！？…ー〜・～]+$", "", s)
    s = re.sub(r"^[……]+", "", s)
    s = re.sub(r"[。、！？…]+$", "", s)
    return s.strip()


def lookup_interj(m):
    """查短语气词：先精确匹配，再规范化匹配。"""
    p = plain(m)
    if p in INTERJECTION_DICT:
        return INTERJECTION_DICT[p]
    return INTERJECTION_DICT.get(_interj_key(p))


def _has_jp(s):
    """是否含日文字符（平假名/片假名/汉字）。"""
    return any(("\u3040" <= c <= "\u30ff") or ("\u4e00" <= c <= "\u9fff") for c in s)


def _has_kana(s):
    """是否含假名（平假名/片假名）。"""
    return any("\u3040" <= c <= "\u30ff" for c in s)


def protect_newlines(s):
    """翻译前：把字面 \\n / \\N 换成占位符，避免被模型改成真实换行。"""
    return s.replace("\\n", TOK_NL).replace("\\N", TOK_NN)


def restore_newlines(s):
    """翻译后：把占位符还原为字面 \\n / \\N，并把模型可能引入的真实换行也归为字面 \\n。"""
    s = s.replace(TOK_NL, "\\n").replace(TOK_NN, "\\N")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "")
    return s


# 行尾标点交换到行首：避免 `\\n。` 显示成「行首孤立句号」
_LEADING_PUNCT = "。，、！？；：…」』）)]} "

# 模型可能残留的日语语气词（按常见程度排序），长串优先以防短串误伤
JA_INTERJECTION_CLEAN = [
    ("エヘヘヘ", "诶嘿嘿嘿"),
    ("エヘへへ", "诶嘿嘿"),
    ("エヘヘ", "诶嘿嘿"),
    ("エヘへ", "诶嘿"),
    ("ふふふ", "呵呵呵"),
    ("へへっ", "嘿嘿"),
    ("へへへ", "嘿嘿嘿"),
    ("へへ", "嘿嘿"),
    ("ふふっ", "呵呵"),
    ("ニヤニヤ", "嘻嘻"),
    ("きゃあ", "呀"),
    ("キャア", "呀"),
    ("きゃっ", "呀"),
    ("ひゃあ", "呀"),
    ("お土産", "伴手礼"),
]


def fix_leading_punct(s):
    """把 \\n/\\N 之后的行尾标点交换到 \\n/\\N 之前（避免孤立标点在行首）。"""
    for p in _LEADING_PUNCT:
        s = s.replace("\\n" + p, p + "\\n")
        s = s.replace("\\N" + p, p + "\\N")
    return s


def clean_japanese_interjections(s):
    """清理译文里残留的日语语气词（模型偶尔不翻的短句）。"""
    for ja, zh in JA_INTERJECTION_CLEAN:
        s = s.replace(ja, zh)
    s = s.replace("っ", "")  # 促音符号（中文无对应，直接删除）
    return s


# 确定性人名替换（模型常忽略 NAME_GLOSSARY，把「さや」「マヨ」等保留假名）。
# 按长度降序，先替换带敬称的全形，避免「さやちゃん」被「さや」先命中。
NAME_REPLACE = [
    ("おばあちゃん", "奶奶"),
    ("きょーちゃん", "小恭"),
    ("ゆーくん", "优"),
    ("マヨちゃん", "麻夜"),
    ("さやちゃん", "小夜"),
    ("なつみちゃん", "夏美"),
    ("アキくん", "阿基"),
    ("都築なつみ", "都筑夏美"),
    ("蔵本今日子", "藏本今日子"),
    ("マヨ", "麻夜"),
    ("アキ", "阿基"),
    ("なつみ", "夏美"),
    ("さや", "小夜"),
    ("都築", "都筑"),
    ("蔵本", "藏本"),
    ("澤村", "泽村"),
    ("桜井", "樱井"),
    ("あや", "彩"),
    ("ユキ", "雪"),
    ("ペロ", "佩罗"),
    ("ぺロ", "佩罗"),
]


def clean_names(s):
    """把译文里残留的日文人名替换为中文译名。"""
    for ja, zh in NAME_REPLACE:
        s = s.replace(ja, zh)
    return s


# 换行标记正则：字面 \n 或 \N（两字节，非真实换行）
MARKER_RE = re.compile(r"\\[nN]")


def _marker_scan(s):
    """扫描 s，返回 (纯文本, [每个标记前的字符数])。"""
    text = []
    counts = []
    acc = 0
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in "nN":
            counts.append(acc)
            i += 2
        else:
            text.append(s[i])
            acc += 1
            i += 1
    return "".join(text), counts


# 优先吸附的标点边界，避免把标记插进词中间
_PUNCT = set("，。、！？；：…—―·「」『』（）　 ")


def _snap(text, idx):
    """把 idx 吸附到最近的标点边界，避免拆词。"""
    if idx <= 0 or idx >= len(text):
        return idx
    for d in range(8):
        for cand in (idx - d, idx + d):
            if 0 < cand < len(text) and text[cand] in _PUNCT:
                return cand
    return idx


def ensure_markers(trans, src):
    """保证 trans 的字面 \\n/\\N 标记与 src 数量、类型、顺序完全一致。

    模型若完整保留了标记则信任其位置；否则按源文各标记的字符比例位置
    重新插入（吸附标点），既保证引擎换行结构不破坏，又避免拆词。
    """
    src_text, src_counts = _marker_scan(src)
    trans_text, trans_counts = _marker_scan(trans)
    n = len(src_counts)
    if n == 0:
        return trans_text
    if len(trans_counts) == n:
        return trans  # 模型完整保留，信任其自然断行位置
    # 重建：把 src 的标记序列按比例插入 trans_text
    src_markers = MARKER_RE.findall(src)
    tlen = len(trans_text)
    if tlen == 0:
        return trans_text
    base = max(1, len(src_text))
    positions = [_snap(trans_text, int(round(c / base * tlen))) for c in src_counts]
    for i in range(1, len(positions)):  # 保证严格递增，避免重叠
        if positions[i] <= positions[i - 1]:
            positions[i] = min(tlen, positions[i - 1] + 1)
    out = trans_text
    for i in range(n - 1, -1, -1):  # 从后往前插入，避免索引漂移
        out = out[:positions[i]] + src_markers[i] + out[positions[i]:]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 日繁映射
# ─────────────────────────────────────────────────────────────────────────────
def load_kanji_table(path=KANJI_TABLE):
    m = {}
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if not ln.strip():
                continue
            p = ln.split("\t")
            if len(p) == 2 and len(p[0]) == 1 and len(p[1]) == 1:
                m[p[0]] = p[1]
    return m


def to_kanji(s, table):
    """简体 → 日繁（Shift-JIS 可编码）。返回 (结果串, 未覆盖字符集合)。"""
    out = []
    unmapped = []
    for ch in s:
        if ch in table:
            out.append(table[ch])
            continue
        try:
            ch.encode("cp932")
            out.append(ch)
        except UnicodeEncodeError:
            unmapped.append(ch)
            out.append("\u25a1")  # □ 占位，便于后续人工检查
    return "".join(out), unmapped


# ─────────────────────────────────────────────────────────────────────────────
# LLM 调用（OpenAI 兼容接口）
# ─────────────────────────────────────────────────────────────────────────────
# deepseek-v4-flash 关推理后（thinking disabled）是弱快速模型：
# 提示词太复杂（多规则+完整人名表）会让它在批量大时直接回显原文。
# 用简短提示词 + 温度 0.5 即可稳定大 batch 翻译（200 条/批，零回显）。
MAX_TOKENS = 16384


def chat(messages, model=MODEL, temperature=0.5, max_tokens=MAX_TOKENS, retries=6):
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API_BASE + "/v1/chat/completions", data=payload,
            headers={"Authorization": "Bearer " + API_KEY,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
            time.sleep(1.5 * (attempt + 1))
            continue
        except Exception as e:
            last_err = repr(e)
            time.sleep(1.5 * (attempt + 1))
            continue
        msg = body.get("choices", [{}])[0].get("message", {})
        content = (msg.get("content") or "").strip()
        if content:
            return content
        # 空响应（含仅 reasoning_content 的情况）：重试
        last_err = "空响应"
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("LLM 调用失败（%s）：%s" % (model, last_err))


def _parse_json_string(text, i):
    """解析 text[i] 开头的 JSON 字符串（i 指向开引号），返回 (字符串, 结束索引+1)。

    容忍字符串内的真实换行：把真实 \n / \r\n 归为字面 \\n 标记。
    """
    out = []
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 1
            if j >= len(text):
                return None, j
            e = text[j]
            if e == "n":
                out.append("\\n")
            elif e == "t":
                out.append("\t")
            elif e == "r":
                out.append("\\n")
            elif e == "u":
                try:
                    out.append(chr(int(text[j + 1:j + 5], 16)))
                except Exception:
                    out.append(e)
                j += 4
            else:
                out.append(e)
            j += 1
        elif c == '"':
            return "".join(out), j + 1
        elif c in "\r\n":
            out.append("\\n")
            j += 1
        else:
            out.append(c)
            j += 1
    return None, j


def extract_json_array(text):
    """从模型输出中提取字符串数组（强容错）。

    优先严格 json.loads 解析 [ ... ]；失败则退化为「提取所有双引号字符串」，
    容忍多余标点（如闭合引号后多出的 。）、缺失逗号等模型偶发 JSON 瑕疵。
    调用方再用数量校验兜底。
    """
    # 1) 严格解析：定位每个 [ 配对的 ]，尝试 json.loads
    i = text.find("[")
    while i != -1:
        j = text.rfind("]", i)
        if j > i:
            try:
                arr = json.loads(text[i:j + 1])
                if isinstance(arr, list) and arr and all(isinstance(x, str) for x in arr):
                    return arr
            except Exception:
                pass
        i = text.find("[", i + 1)
    # 2) 容错提取：只在 [ ... ] 范围内扫出所有双引号字符串（忽略中间噪音字符）
    i = text.find("[")
    j = text.rfind("]", i) if i != -1 else -1
    scope = text[i:j + 1] if (i != -1 and j > i) else text
    arr = []
    k = 0
    while k < len(scope):
        if scope[k] == '"':
            s, nk = _parse_json_string(scope, k)
            if s is not None:
                arr.append(s)
                k = nk
            else:
                k += 1
        else:
            k += 1
    return arr if arr else None


def build_prompt(jp_list):
    glossary = "、".join("%s=%s" % (k, v) for k, v in NAME_GLOSSARY.items())
    system = (
        "你是日译中翻译。把下面 JSON 数组里的日文台词逐条翻译成简体中文，"
        "输出一个等长的 JSON 数组，逐条对应，只输出译文、不要解释、不要编号。\n"
        "规则：保留原文中的 ⟨换行⟩ 和 ⟨换行2⟩ 标记（这两个是换行标记，译文对应位置原样保留，"
        "不要删、不要改成真实换行）；人名固定译法：" + glossary + "。"
    )
    user = json.dumps(jp_list, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def translate_batch(jp_list):
    """翻译一批，返回 (译文列表, 回显标记列表)。解析失败/数量不符才抛异常；
    单条回显不抛异常，而是用 echo_flags 标记出来，由调用方逐条兜底。"""
    protected = [protect_newlines(s) for s in jp_list]
    content = chat(build_prompt(protected))
    arr = extract_json_array(content)
    if arr is None and len(jp_list) == 1:
        # 单条时模型可能输出裸字符串（无 []），直接尝试解析
        try:
            s = json.loads(content)
            if isinstance(s, str):
                arr = [s]
        except Exception:
            pass
    if arr is None:
        raise RuntimeError("模型未返回 JSON 数组：%r" % content[:200])
    if len(arr) != len(jp_list):
        raise RuntimeError("译文数量不符：期望 %d 实得 %d" % (len(jp_list), len(arr)))
    trans = [restore_newlines(s if isinstance(s, str) else str(s)) for s in arr]
    # 换行标记对齐：保证每条的 \n/\N 数量/类型/顺序与源文一致（模型可能丢失标记）
    trans = [ensure_markers(t, jp) for t, jp in zip(trans, jp_list)]
    # 行尾标点交换（避免 \n。 显示成行首孤立标点）
    trans = [fix_leading_punct(t) for t in trans]
    # 清理译文里残留的日语语气词（模型偶尔不翻的短句）
    trans = [clean_japanese_interjections(t) for t in trans]
    # 清理译文里残留的日文人名（模型常忽略人名表）
    trans = [clean_names(t) for t in trans]
    # 回显检测：译文与原文完全相同且原文含假名 → 模型没翻译，标记出来
    echo_flags = [t == jp and _has_kana(jp) for t, jp in zip(trans, jp_list)]
    return trans, echo_flags


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def main(argv):
    limit = None
    batch = 200
    model = MODEL
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--limit":
            limit = int(argv[i + 1]); i += 2
        elif a == "--batch":
            batch = int(argv[i + 1]); i += 2
        elif a == "--model":
            model = argv[i + 1]; i += 2
        else:
            i += 1

    table = load_kanji_table()

    # 载入缓存（去重 + 断点续传）
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)

    # 收集全部 JSON 与去重后的待译文本
    files = sorted(glob.glob(os.path.join(TEXT_DIR, "*.json")))
    docs = {fp: json.load(open(fp, encoding="utf-8")) for fp in files}

    # 待译集合（按出现顺序去重；纯 ASCII/符号等无日文字符的串不翻译，原样保留）
    seen = set()
    todo = []
    for fp in files:
        for r in docs[fp]:
            m = r["message"]
            if m and m not in seen:
                seen.add(m)
                if _has_jp(m):
                    todo.append(m)
                else:
                    # 无日文（START / 省略号 / 破折号等）：跳过翻译，原样
                    cache[m] = {"trans": m, "trans_kanji": m}
    if limit:
        todo = todo[:limit]

    # 待译集合：先做语气词词典确定性命中，命中者直接入库（不走 LLM）
    unmapped_all = set()
    pending = []
    for m in todo:
        if m in cache:
            continue
        d = lookup_interj(m)
        if d is not None:
            k, unm = to_kanji(d, table)
            cache[m] = {"trans": d, "trans_kanji": k}
            unmapped_all.update(unm)
        else:
            pending.append(m)
    print("唯一台词 %d 条，已缓存 %d 条，待翻译 %d 条" %
          (len(todo), len(todo) - len(pending), len(pending)))

    # 批量翻译（队列 + 拆半重试 + 回显逐条兜底；chat 内部已含空响应重试）
    import collections
    queue = collections.deque()
    for s in range(0, len(pending), batch):
        queue.append(pending[s:s + batch])

    failed = []
    t0 = time.time()
    done = 0
    total = len(pending)
    while queue:
        chunk = queue.popleft()
        trans = None
        echo_flags = None
        for attempt in range(3):
            try:
                trans, echo_flags = translate_batch(chunk)
                break
            except Exception as e:
                print("  批 %d 条 失败(第%d次)：%s" % (len(chunk), attempt + 1, str(e)[:120]),
                      flush=True)
                time.sleep(1)
        if trans is None:
            # 解析失败/数量不符：拆半重试
            if len(chunk) > 1:
                mid = len(chunk) // 2
                queue.appendleft(chunk[mid:])
                queue.appendleft(chunk[:mid])
                print("  拆半重试：%d -> %d + %d" % (len(chunk), mid, len(chunk) - mid),
                      flush=True)
            else:
                failed.append(chunk[0])
                print("  跳过单条（重试耗尽）：%r" % chunk[0][:40], flush=True)
        else:
            echo_lines = []
            for jp, zh, echo in zip(chunk, trans, echo_flags):
                if not echo:
                    k, unm = to_kanji(zh, table)
                    cache[jp] = {"trans": zh, "trans_kanji": k}
                    unmapped_all.update(unm)
                    done += 1
                else:
                    echo_lines.append(jp)
            if echo_lines:
                if len(chunk) == 1:
                    # 单条仍回显：词典兜底，否则记入失败待人工复核
                    jp = chunk[0]
                    d = lookup_interj(jp)
                    if d is not None:
                        k, unm = to_kanji(d, table)
                        cache[jp] = {"trans": d, "trans_kanji": k}
                        unmapped_all.update(unm)
                        done += 1
                    else:
                        failed.append(jp)
                        print("  回显跳过（词典未命中）：%r" % jp[:40], flush=True)
                else:
                    # 批量里的回显：放回队列逐条重试（单条更易翻译成功）
                    for jp in echo_lines:
                        queue.appendleft([jp])
                    print("  回显 %d 条，放回逐条重试" % len(echo_lines), flush=True)
        # 增量保存
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        el = time.time() - t0
        if done and done % 50 == 0:
            print("  [%d/%d] %.0fs" % (done, total, el), flush=True)

    # 写回 JSON
    for fp in files:
        changed = False
        for r in docs[fp]:
            m = r["message"]
            if m in cache:
                r["trans"] = cache[m]["trans"]
                r["trans_kanji"] = cache[m]["trans_kanji"]
                changed = True
        if changed:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(docs[fp], f, ensure_ascii=False, indent=2)
                f.write("\n")

    print("完成。缓存 %d 条，未覆盖字符 %d 个：%s，跳过 %d 条" %
          (len(cache), len(unmapped_all),
           "".join(sorted(unmapped_all)) if unmapped_all else "无", len(failed)))
    if failed:
        with open(os.path.join(os.path.dirname(CACHE_PATH), "translate_failed.txt"),
                  "w", encoding="utf-8") as f:
            f.write("\n".join(failed) + "\n")
        print("失败条目已写入 _work/translate_failed.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
