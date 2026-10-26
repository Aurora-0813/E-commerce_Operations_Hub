# -*- coding: utf-8 -*-
"""
验证文本归一化。

运行（在 backend/ 目录下）：
    python tests/check_normalize.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.dqe.normalize import normalize, text_stats   # noqa: E402

# (原始, 期望, 说明)
CASES = [
    ("好\u200b评", "好评", "零宽空格被去掉"),
    ("好\u200d评", "好评", "零宽连接符被去掉"),
    ("ＡＢＣ", "ABC", "全角字母转半角"),
    ("<b>好评</b>", "好评", "HTML 标签被剥掉"),
    ("太!!!!!!!!!差", "太!差", "连续标点被压缩"),
    ("哈哈哈", "哈哈哈", "3 个重复不压缩（正常语气）"),
    ("  好评  ", "好评", "首尾空白去掉"),
]


def main() -> None:
    print("=== 归一化用例 ===")
    all_ok = True
    for raw, expect, desc in CASES:
        got = normalize(raw)
        ok = got == expect
        all_ok = all_ok and ok
        print(f"{'✅' if ok else '❌'} {desc}")
        print(f"   输入 {raw!r}")
        print(f"   输出 {got!r}  期望 {expect!r}")

    print("\n=== 文本统计 ===")
    for t in ("好评", "太!差", "Great product", "！！！"):
        print(f"{t!r:20} -> {text_stats(t)}")

    print("\n" + ("✅ 全部通过" if all_ok else "❌ 有失败用例"))


if __name__ == "__main__":
    main()
