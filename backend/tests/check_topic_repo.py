# -*- coding: utf-8 -*-
"""
验证 ReviewTopicRepo（只读，不写数据）。

此时 review_topic 表应该是空的，所以：
    fetch_pending(增量) ≈ 366 条
    fetch_pending(全量) ≈ 366 条（一样，因为表空）
    count_total()      = 0

运行（在 backend/ 目录下）：
    python tests/check_topic_repo.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.persistence.repo_topic import ReviewTopicRepo   # noqa: E402


def main() -> None:
    repo = ReviewTopicRepo()

    # ---- 1. 取待分类 ----
    pending = repo.fetch_pending()
    pending_force = repo.fetch_pending(force=True)
    print(f"待分类（增量）: {len(pending)} 条")
    print(f"待分类（全量）: {len(pending_force)} 条")

    if not pending:
        print("⚠️ 没有待分类的差评，检查：DQE 跑过吗？rating<=2 的有吗？")
        return

    # ---- 2. 抽样看内容 ----
    print("\n抽样 5 条：")
    for r in pending[:5]:
        text = (r.content_clean or "").replace("\n", " ")[:40]
        print(f"  [{r.platform:<6}] rating={r.rating} spu={r.platform_spu_id} | {text}")

    # ---- 3. 平台分布 ----
    from collections import Counter
    plat = Counter(r.platform for r in pending)
    print(f"\n平台分布: {dict(plat)}")

    # ---- 4. 统计（表空，应为 0 / []）----
    print(f"\n已分类总数: {repo.count_total()}")
    print(f"topic×平台 格子: {repo.count_by_topic_platform()}")

    # ---- 5. 抽检参数 ----
    print(f"\n--limit 7 → {len(repo.fetch_pending(limit=7))} 条")
    print(f"rating_max=3 → {len(repo.fetch_pending(rating_max=3))} 条")

    # ---- 6. 品类分布（表空，应为 []）----
    print(f"品类分布(quality/taobao): {repo.count_by_category('quality', 'taobao')}")


if __name__ == "__main__":
    main()
