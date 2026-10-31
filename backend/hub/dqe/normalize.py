# -*- coding: utf-8 -*-
"""
文本归一化（normalize.py）
=========================
DQE 的第一步。目的：把"人眼看起来一样、机器看起来不一样"的文本统一。

为什么必须先归一化：
    水军/灌水常用"障眼法"绕过规则：
        好\u200b评          ← 中间插零宽空格，规则匹配不到"好评"
        ＡＢＣ              ← 全角字母，规则匹配不到"ABC"
        <b>好评</b>         ← HTML 标签，长度和内容都变了
        太!!!!!!!!!!!!差    ← 重复标点刷屏，标点占比被拉高

    归一化把这些差异抹平，后面的规则才能在同一尺度上判断。

四个操作：
    1. NFKC：全角 ａ → 半角 a，① → 1（Unicode 兼容字符统一）
    2. 去零宽字符：U+200B / U+200C / U+200D / U+2060 / U+FEFF
    3. 剥 HTML 标签：<b>好评</b> → 好评
    4. 压缩连续重复字符：太!!!!!差 → 太!差（同一字符连续 5 次以上才压）
"""
import re
import unicodedata

# ---- 零宽字符：水军最爱用来"断开"关键词 ----
# \u200b 零宽空格、\u200c 零宽非连接符、\u200d 零宽连接符、
# \u2060 词连接符、\ufeff BOM
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# ---- HTML 标签：<b>、</div>、<br/> 等 ----
# 限制长度 1~50，避免贪婪匹配吃掉正常文本里的尖括号
_HTML_TAG = re.compile(r"<[^>]{1,50}>")

# ---- 连续重复字符：只压缩"中文/字母/数字"的重复 ----
# 为什么只压缩这三类：
#   标点和 emoji 的连续重复，恰恰是 R003（纯符号评价）的检测信号，
#   如果一起压掉，"太!!!!!!!!差" 会变成 "太!差"，标点占比从 80% 掉到 33%，
#   R003 就永远抓不到纯符号刷屏了。
# 为什么是 5 次以上："哈哈哈"（3 个）是正常语气，压掉会失真。
_REPEAT = re.compile(r"([\u4e00-\u9fffA-Za-z0-9])\1{4,}")


def normalize(text: str) -> str:
    """把文本归一化，返回新字符串（不修改入参）。

    例：
        "好\\u200b评"          → "好评"（去零宽字符）
        "ＡＢＣ"               → "ABC"（NFKC 全角转半角）
        "太!!!!!!!!!差"        → **"太!!!!!!!!!差"（标点不压缩！）**
        "太啊啊啊啊啊差"        → "太啊差"（中文 5 连压成 1）
        "<b>太!!!!!!!!!差</b>" → "太!!!!!!!!!差"（只剥标签，不动标点）

    ⚠️ **标点是故意不压缩的**（见 `_REPEAT` 上方的注释）：
        连续标点/符号是 R003（纯符号评价）的检测信号，压掉就抓不到了。
        这条 docstring 原先写的是 `"太!!!!!!!!!差" → "太!差"`，
        与代码行为相反，导致 `tests/check_normalize.py` 按错的期望断言、
        红了一年没人发现（2026-10-31 修）。**代码是对的，文档和测试是错的。**
    """
    if not text:
        return ""

    # 1. NFKC：全角转半角、兼容字符统一
    #    NFKC 是 Unicode 的"兼容分解 + 规范合成"，
    #    能把 Ａ→A、１→1、①→1、ﬁ→fi 统一掉。
    text = unicodedata.normalize("NFKC", text)

    # 2. 去零宽字符（插在关键词中间的那种）
    text = _ZERO_WIDTH.sub("", text)

    # 3. 剥 HTML 标签
    text = _HTML_TAG.sub("", text)

    # 4. 压缩连续重复字符（5 次以上）
    #    \1 表示"和第一个捕获组相同的字符"，
    #    所以 "太!!!!!!!!!差" → "太!差"
    text = _REPEAT.sub(r"\1", text)

    # 5. 去首尾空白
    return text.strip()


def _is_punct_or_symbol(ch: str) -> bool:
    """判断一个字符是不是"标点或符号（含 emoji）"。

    Unicode 类别：
        P*  = 标点（Punctuation）
        So  = 其他符号（Symbol, other），emoji 大多属于这里
        Sk  = 修饰符号
    """
    cat = unicodedata.category(ch)
    return cat.startswith("P") or cat in ("So", "Sk")


def text_stats(text: str) -> dict:
    """统计文本特征（规则轨要用）。

    :return: {
        "length": 归一化后的字符数,
        "chinese_ratio": 中文字符占比 0~1,
        "punct_ratio": 标点+符号占比 0~1,
    }
    """
    n = len(text)
    if n == 0:
        return {"length": 0, "chinese_ratio": 0.0, "punct_ratio": 0.0}

    # 中文字符范围：CJK 统一表意文字基本区
    chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    punct = sum(1 for ch in text if _is_punct_or_symbol(ch))

    return {
        "length": n,
        "chinese_ratio": round(chinese / n, 4),
        "punct_ratio": round(punct / n, 4),
    }
