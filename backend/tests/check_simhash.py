# -*- coding: utf-8 -*-
"""
对比 SimHash 和 Jaccard 在短文本上的表现。

运行（在 backend/ 目录下）：
    python tests/check_simhash.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.dqe.simhash import hamming, jaccard, simhash   # noqa: E402

PAIRS = [
    ("质量不错，值得购买", "质量不错，值得购买", "完全相同", True),
    ("质量不错，值得购买", "质量不错，值得购买！", "加个标点", True),
    ("质量不错，值得购买", "质量不错，值的购买", "错别字", True),
    ("这件衣服破洞，还开线了，质量很差",
     "这件衣服破洞，开线了，质量很差", "删一个字", True),
    ("质量不错，值得购买", "这件衣服掉色严重，穿一次就坏了", "完全不同（评价）", False),
    ("质量不错，值得购买", "物流太慢，等了一个星期才到", "完全不同（物流）", False),
]


def main() -> None:
    print(f"{'用例':<16}{'汉明距离':>10}{'Jaccard':>10}{'期望重复':>10}")
    print("-" * 50)
    for a, b, desc, expect_dup in PAIRS:
        d = hamming(simhash(a), simhash(b))
        j = jaccard(a, b)
        print(f"{desc:<16}{d:>10}{j:>10.2f}{str(expect_dup):>10}")

    print("\n结论：")
    print("  - 汉明距离：相似的 8~14、不同的 30+，阈值 3 抓不到短文本的近似重复")
    print("  - Jaccard：相似的 ≥0.60、不同的 0.00，用阈值 0.60 分得很开")


if __name__ == "__main__":
    main()
