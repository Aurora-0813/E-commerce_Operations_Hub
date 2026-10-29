# -*- coding: utf-8 -*-
"""
优先级打分（priority.py）
=======================
给每个问题簇打 0~100 分，分 P0~P3 四档，告诉运营"先处理哪个"。

公式（设计文档 §3.4.3）：
    P = 100 * (0.40*reach + 0.30*severity + 0.20*trend + 0.10*actionability)

    reach         影响面：该簇涉及的商品销量占全量比例
                  （没有销量数据时退回"评价量占比"，按最大簇归一化）
    severity      严重度：簇内评价的平均严重度（0~1，已归一化）
    trend         趋势：环比增长率（P0 第一次跑没有历史 → 固定 0.5）
    actionability 可执行性：有工具能自动处理=1.0 / 需人工=0.6 / 无工具=0.2
                  （M5/M6 还没做，统一 0.6）

分档：
    P0 >= 70   立刻处理
    P1 >= 55   本周处理
    P2 >= 40   排期
    P3 <  40   观察
"""
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import IssueCluster, SalesDaily
from hub.persistence.repo_cluster import ClusterRepo

TREND_DEFAULT = 0.5        # 没有历史数据时的趋势中性值
ACTIONABILITY = 0.6        # M5/M6 未做，统一"需要人工"
TIER_CUTS = [(70.0, "P0"), (55.0, "P1"), (40.0, "P2"), (0.0, "P3")]


@dataclass
class PriorityStats:
    clusters: int = 0
    scored: int = 0
    tiers: dict = field(default_factory=dict)
    top: list = field(default_factory=list)     # 前 3 名（label, score, tier）
    elapsed_s: float = 0.0


def _tier(score: float) -> str:
    for cut, name in TIER_CUTS:
        if score >= cut:
            return name
    return "P3"


def _sales_by_spu() -> dict[str, float]:
    """每个商品的累计 GMV。"""
    with get_session() as s:
        rows = s.execute(
            select(SalesDaily.canonical_spu_id, func.sum(SalesDaily.gmv))
            .group_by(SalesDaily.canonical_spu_id)
        ).all()
    return {r[0]: float(r[1] or 0) for r in rows}


def run_priority(*, trend: float = TREND_DEFAULT,
                 actionability: float = ACTIONABILITY) -> PriorityStats:
    """给最近一次聚类的每个簇打分，写回 issue_cluster。"""
    t0 = time.time()
    repo = ClusterRepo()
    clusters = repo.fetch_latest_clusters()
    stats = PriorityStats(clusters=len(clusters))
    if not clusters:
        print("没有聚类结果（先跑 hub cluster）")
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    sales = _sales_by_spu()
    total_sales = sum(sales.values())
    max_count = max(c.review_count for c in clusters) or 1

    # ---- ① 先算所有分数 ----
    scored: list[tuple] = []
    for c in clusters:
        # ---- reach ----
        spu_sales = sum(sales.get(spu, 0) for spu in (c.spu_ids or []))
        sales_reach = (spu_sales / total_sales) if total_sales else 0.0
        review_reach = c.review_count / max_count
        # 有销量数据用销量占比，没有就退回"评价量占比"
        reach = sales_reach if sales_reach > 0 else review_reach

        severity = float(c.severity or 0.5)
        score = 100 * (0.40 * reach + 0.30 * severity
                       + 0.20 * trend + 0.10 * actionability)
        score = round(min(100.0, max(0.0, score)), 1)
        scored.append((c, score, reach, severity))

    # ---- ② 按分位定档（而不是绝对阈值）----
    # 为什么：trend（0.5）和 actionability（0.6）现在都是常量（M5/M6 还没做），
    #   分数全挤在 32~62 之间，绝对阈值 70 永远够不到 → P0 永远是 0 个。
    #   等 M5/M6 做完、actionability 有区分度，再改回绝对阈值。
    scored.sort(key=lambda x: -x[1])
    n = len(scored)
    cut_p0 = max(1, round(n * 0.10))            # 前 10%      → P0
    cut_p1 = max(cut_p0 + 1, round(n * 0.30))   # 10%~30%     → P1
    cut_p2 = max(cut_p1 + 1, round(n * 0.70))   # 30%~70%     → P2
                                                # 后 30%      → P3

    with get_session() as s:
        for rank, (c, score, reach, severity) in enumerate(scored):
            if rank < cut_p0:
                tier = "P0"
            elif rank < cut_p1:
                tier = "P1"
            elif rank < cut_p2:
                tier = "P2"
            else:
                tier = "P3"

            row = s.get(IssueCluster, c.id)
            row.priority_score = score
            row.priority_tier = tier

            stats.scored += 1
            stats.tiers[tier] = stats.tiers.get(tier, 0) + 1
            stats.top.append((c.label, score, tier, c.review_count, reach))
            print(f"  {tier}  {score:>5.1f}  {c.label:<14}"
                  f"（{c.review_count:>3} 条｜reach {reach:.2f}｜sev {severity:.2f}）")

        s.commit()

    stats.top.sort(key=lambda x: -x[1])
    stats.top = stats.top[:3]
    stats.elapsed_s = round(time.time() - t0, 2)
    return stats
