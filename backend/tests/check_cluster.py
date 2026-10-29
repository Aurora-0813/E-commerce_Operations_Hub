# -*- coding: utf-8 -*-
"""
查看最近一次聚类的结果（issue_cluster 表）。

运行（在 backend/ 目录下，先跑 hub cluster）：
    python tests/check_cluster.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.persistence.repo_cluster import ClusterRepo   # noqa: E402


def main() -> None:
    repo = ClusterRepo()
    clusters = repo.fetch_latest_clusters()
    if not clusters:
        print("issue_cluster 是空的（先跑 python -m hub.cli cluster）")
        return

    total = sum(c.review_count for c in clusters)
    print(f"最近一次聚类：{len(clusters)} 个簇，覆盖 {total} 条差评"
          f"（表里累计 {repo.count()} 行）\n")

    print(f"{'条数':>5}  {'簇名':<16}{'品类':<8}{'平台':<16}{'severity':>9}")
    print("-" * 60)
    for c in clusters:
        plats = "/".join(c.platforms)
        sev = f"{float(c.severity):.2f}" if c.severity is not None else "-"
        print(f"{c.review_count:>5}  {c.label:<16}{c.business_domain:<8}"
              f"{plats:<16}{sev:>9}")

    print("\n=== 前 3 个簇的代表评价 ID ===")
    for c in clusters[:3]:
        ids = ", ".join(str(i)[:8] for i in c.representative_review_ids[:3])
        print(f"  {c.label}：{ids}…")

    # 健康检查
    sizes = [c.review_count for c in clusters]
    print("\n=== 健康检查 ===")
    print(f"  簇数 {len(clusters)}（目标 5~15）")
    print(f"  最大簇 {max(sizes)} 条｜最小簇 {min(sizes)} 条")
    if not (5 <= len(clusters) <= 15):
        print("  ⚠️ 簇数不在 5~15，调整 --min-size 再试")


if __name__ == "__main__":
    main()
