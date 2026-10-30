# -*- coding: utf-8 -*-
"""
终端输出适配（core/ui.py）
==========================
**让同一份输出在"能显示 emoji 的终端"和"编不了 emoji 的编码"下都看得懂。**

## 为什么需要

    中文 Windows 上，CLI 输出一旦被重定向/接管道（`hub status | Select-String`、
    写日志、CI 抓输出），Python 就用 locale 默认编码（GBK）。
    GBK 编不了的东西会抛 UnicodeEncodeError —— **一行 traceback 比"少显示一个字符"糟糕得多**。

    所以 `cli.main()` 里装了 `sys.stdout.reconfigure(errors="replace")` 兜底。
    但 replace 会把 `✅` 变成 `?`，**分组标记就没了**：
        `? 要我做（不批就卡住）`   ← 看不出是第几组

## 做法

    输出层把"当前编码编不了的字符"换成**等价的 ASCII**，而不是 `?`：
        ✅ → [OK]    ❌ → [X]    ⚠️ → [!]    🔴🟡🔵⚪ → *
    能编码时原样输出（终端里照样是 emoji）。

    判断依据是 `sys.stdout.encoding`，**不是配置开关** ——
    多一个"要记得设置"的开关，就是多一个会忘的地方。

## 用法

    from hub.core.ui import install
    install()      # 装在 main() 的最开头，之后所有 print 都走它
"""
import sys

# 编不了时用的等价文本（要保证**纯 ASCII**，否则又编不了）
FALLBACK = {
    "✅": "[OK]",
    "❌": "[X]",
    "⚠️": "[!]",
    "⚠": "[!]",
    "🔴": "[1]",     # 分组标记：保留【顺序】信息，不只是换个符号
    "🟡": "[2]",
    "🔵": "[3]",
    "⚪": "[4]",
    "⬆️": "^",
    "⏭": ">>",
    "💰": "$",
    "🔧": "*",
}


def _can_encode(ch: str, encoding: str) -> bool:
    try:
        ch.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class SafeStream:
    """包一层 stdout：编不了的字符换成等价文本。

    只替换【表里列出的那些】；表外的仍靠 errors="replace" 兜底成 `?`。
    为什么只列这些：列出来的是"有语义的标记"（成/败/警告/分组），
    换掉有意义；表情装饰性的东西变成 `?` 无所谓。

    ⚠️ 用 `str.replace` 逐条替换，**不能用 `str.translate`**：
        `translate` 的表键必须是【单个字符】（它内部按 ord 建表），
        而 `⚠️` 是两个码位（U+26A0 + 变体选择符 U+FE0F）→ `ord()` 直接 TypeError。
        实测踩过：装输出适配的第一个版本就是 `translate`，CLI 一启动就崩。
        长键放前面，避免短键先匹配掉一部分。
    """

    def __init__(self, stream):
        self._stream = stream
        self._encoding = getattr(stream, "encoding", None) or "utf-8"
        # 当前编码编不了的才替换（能编码的原样保留）；长键优先
        self._pairs = sorted(
            ((k, v) for k, v in FALLBACK.items()
             if not _can_encode(k, self._encoding)),
            key=lambda kv: -len(kv[0]),
        )

    def write(self, text: str) -> int:
        if self._pairs and text:
            for old, new in self._pairs:
                if old in text:
                    text = text.replace(old, new)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install() -> None:
    """装到 stdout / stderr 上。**幂等**（重复调用不会套两层）。"""
    for name in ("stdout", "stderr"):
        cur = getattr(sys, name)
        if isinstance(cur, SafeStream):
            continue
        # 先让底层不因编不了而崩（有些字符不在 FALLBACK 里）
        try:
            cur.reconfigure(errors="replace")       # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass                                     # 老 Python / 非 tty，忽略
        setattr(sys, name, SafeStream(cur))
