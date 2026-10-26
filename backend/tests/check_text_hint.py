# -*- coding: utf-8 -*-
"""
验证"文字补类别"的关键词规则。

运行（在 backend/ 目录下）：
    python tests/check_text_hint.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.multimodal.text_hint import infer_defect_from_text   # noqa: E402

CASES = [
    ("衣服掉色严重，和图片完全两个色", "色差"),
    ("开线了，袖口抽丝", "开线"),
    ("有污渍，领口发黄了", "污渍"),
    ("破了个洞，穿一次就坏了", "破损"),
    ("质量不好，不推荐", None),
    ("物流太慢，等了一个星期", None),
    ("", None),
    (None, None),
]


def main() -> None:
    all_ok = True
    for text, expect in CASES:
        got = infer_defect_from_text(text)
        ok = got == expect
        all_ok = all_ok and ok
        print(f"{'✅' if ok else '❌'} {text!r:<40} → {got}（期望 {expect}）")
    print("\n" + ("✅ 全部通过" if all_ok else "❌ 有失败"))


if __name__ == "__main__":
    main()
