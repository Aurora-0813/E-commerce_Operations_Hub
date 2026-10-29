# -*- coding: utf-8 -*-
"""
量化：有多少差评同时提到多个问题（用关键词粗判，不调模型）。

用途：决定"单标签 vs 多标签"值不值得改。
    如果多主题比例很低（<15%）→ 单标签够用，写进文档即可；
    如果很高（>30%）→ 说明单标签丢失了大量信息，应该改。

运行（在 backend/ 目录下）：
    python tests/check_multi_topic.py
"""
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                  # noqa: E402

from hub.analysis.keywords import TOPIC_KEYWORDS   # noqa: E402
from hub.persistence.db import get_session     # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402
from hub.persistence.models import ReviewTopic         # noqa: E402


def main() -> None:
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.content_clean, ReviewTopic.topic)
            .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
        ).all()

    if not rows:
        print("没有已分类的差评")
        return

    dist = Counter()          # 命中几个主题 → 多少条
    mismatch = 0              # 主标签不在命中集合里
    examples: list[tuple] = []

    for text, main_topic in rows:
        hits = [t for t, kws in TOPIC_KEYWORDS.items()
                if any(k in (text or "") for k in kws)]
        dist[len(hits)] += 1

        # 主标签和关键词命中的"主主题"是否一致（粗判，仅参考）
        if hits and main_topic not in hits:
            mismatch += 1

        if len(hits) >= 2 and len(examples) < 12:
            examples.append((text or "", main_topic, hits))

    total = len(rows)
    print(f"已分类差评 {total} 条\n")
    print("每条评价关键词命中的主题数：")
    for n in sorted(dist):
        print(f"  {n} 个主题：{dist[n]:>4} 条（{dist[n] / total:>5.1%}）")

    multi = sum(v for k, v in dist.items() if k >= 2)
    print(f"\n多主题（≥2）合计：{multi} 条（{multi / total:.1%}）")
    print(f"主标签与关键词不一致：{mismatch} 条（{mismatch / total:.1%}）")

    print("\n多主题样例（关键词粗判，可能误报）：")
    for text, main_topic, hits in examples:
        print(f"\n  命中：{'/'.join(hits)}｜当前主标签：{main_topic}")
        print(f"  原文：{text[:70]}")

    print("\n" + "=" * 56)
    if multi / total < 0.15:
        print("结论：多主题比例低（<15%），单标签够用，写进文档即可。")
    elif multi / total < 0.30:
        print("结论：多主题比例中等（15~30%），建议至少『记录次要主题』。")
    else:
        print("结论：多主题比例高（>30%），单标签丢失信息多，建议改多标签。")


if __name__ == "__main__":
    main()
