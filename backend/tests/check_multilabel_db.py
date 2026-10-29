# -*- coding: utf-8 -*-
"""
看 review_topic 里的多标签分布。

验证：
    ① 多标签条数（应该 ≈ 36%）
    ② 最常见的"问题组合"（比如 quality+size）
    ③ "主标签次数" vs "提到次数"的差异

运行（在 backend/ 目录下）：
    python tests/check_multilabel_db.py
"""
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                  # noqa: E402

from hub.persistence.db import get_session     # noqa: E402
from hub.persistence.models import ReviewTopic  # noqa: E402


def main() -> None:
    with get_session() as s:
        rows = s.scalars(select(ReviewTopic)).all()

    if not rows:
        print("review_topic 是空的")
        return

    multi = [r for r in rows if r.secondary_topics]
    print(f"已分类 {len(rows)} 条｜多标签 {len(multi)} 条（{len(multi) / len(rows):.1%}）\n")

    # ② 最常见的组合
    pairs = Counter()
    for r in multi:
        for t in r.secondary_topics:
            pairs[tuple(sorted([r.topic, t]))] += 1
    print("最常见的组合（主 + 次）：")
    for (a, b), n in pairs.most_common(10):
        print(f"  {a:<14} + {b:<14} {n:>3} 条")

    # ③ 主标签 vs 提到次数
    main = Counter(r.topic for r in rows)
    mentions = Counter(main)                     # 从主标签起算
    for r in multi:
        for t in r.secondary_topics:
            mentions[t] += 1

    print("\n主标签 vs 提到次数：")
    print(f"  {'主题':<16}{'主标签':>8}{'提到':>8}{'差值':>8}")
    for t, n in mentions.most_common():
        print(f"  {t:<16}{main.get(t, 0):>8}{n:>8}{n - main.get(t, 0):>8}")

    total_mentions = sum(mentions.values())
    print(f"\n主标签合计 {len(rows)}｜提到合计 {total_mentions}"
          f"（多出 {total_mentions - len(rows)} 次 = 次要标签数）")


if __name__ == "__main__":
    main()
