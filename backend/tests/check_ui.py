# -*- coding: utf-8 -*-
"""
检查：终端输出适配（check_ui.py）
=================================
`hub/core/ui.py` 在**每一次 CLI 调用**的开头执行，所以它崩 = CLI 全崩。
实测踩过：第一版用 `str.translate`，而 `⚠️` 是两个码位，
`ord()` 直接 TypeError → CLI 一启动就 traceback。

这个测试不需要真的换终端编码：直接给 SafeStream 喂一个"只能编 GBK 的流"，
断言 emoji 被换成了 ASCII 等价文本。

运行（在 backend/ 目录下）：
    python tests/check_ui.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.core.ui import SafeStream, install        # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))


class FakeStream:
    """假的输出流：只需要 `encoding` 和 `write`。

    为什么不用 io.StringIO：`StringIO.encoding` 是**只读属性**，赋不了值
    （实测踩过：`AttributeError: attribute 'encoding' ... is not writable`）。
    """

    def __init__(self, encoding: str):
        self.encoding = encoding
        self._buf: list[str] = []

    def write(self, s: str) -> int:
        self._buf.append(s)
        return len(s)

    def getvalue(self) -> str:
        return "".join(self._buf)


def gbk_stream() -> FakeStream:
    """模拟"只能编 GBK 的 stdout"（中文 Windows 上接管道时就是这样）。"""
    return FakeStream("gbk")


def utf8_stream() -> FakeStream:
    return FakeStream("utf-8")


def main() -> None:
    print("=" * 62)
    print("  终端输出适配")
    print("=" * 62)

    print("\n[1] 能编 UTF-8 时：emoji 原样保留")
    s = utf8_stream()
    SafeStream(s).write("✅ 完成 ⚠️ 注意 🔴 分组")
    out = s.getvalue()
    check("✅ 保留", "✅" in out, out)
    check("🔴 保留", "🔴" in out)

    print("\n[2] 只能编 GBK 时：换成 ASCII 等价文本")
    s = gbk_stream()
    SafeStream(s).write("✅ 完成 ⚠️ 注意 🔴 分组 ❌ 失败")
    out = s.getvalue()
    check("✅ → [OK]", "[OK]" in out, out)
    check("⚠️ → [!]", "[!]" in out)
    # 分组标记用 [1] 而不是 *：替换时**保留顺序信息**，
    # 否则管道下"要我做"和"知会"看起来一模一样，分组就白分了。
    check("🔴 → [1]（保留顺序）", "[1]" in out)
    check("❌ → [X]", "[X]" in out)
    check("没有残留编不了的字符",
          all(_fits(c) for c in out), out)

    print("\n[3] 中文和标点【一个都不动】")
    s = gbk_stream()
    text = "降噪率 41.5%｜差评 973 条｜（P0 2 个）→ 下一步"
    SafeStream(s).write(text)
    check("中文原样", s.getvalue() == text, s.getvalue())

    print("\n[4] 幂等：install() 重复调用不会套两层")
    install()
    first = sys.stdout
    install()
    check("重复调用不会再包一层", sys.stdout is first)

    print("\n[5] 多码位字符（这是踩过的坑）")
    s = gbk_stream()
    SafeStream(s).write("⚠️")     # U+26A0 + U+FE0F 两个码位
    check("不抛异常且被替换", s.getvalue() == "[!]", repr(s.getvalue()))
    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)}｜失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"    ❌ {f}")
    print("=" * 62)
    sys.exit(0 if not FAIL else 1)


def _fits(ch: str) -> bool:
    try:
        ch.encode("gbk")
        return True
    except UnicodeEncodeError:
        return False


if __name__ == "__main__":
    main()
