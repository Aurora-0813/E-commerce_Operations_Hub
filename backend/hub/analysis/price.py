# -*- coding: utf-8 -*-
"""
价格背离检测（price.py）
=======================
同一个商品在两个平台卖不同价，**且贵的那边差评更多** → 值得关注。

公式（设计文档 §3.4.2）：
    dev   = ln(price_p / median(跨平台价格))       # 价格对数偏离
    sev   = 该 SPU 在平台 p 的差评率分位 (0~1)
    flag  = |dev| >= 0.15  AND  sev >= 0.70
    score = 100 * sigmoid(4*(|dev| - 0.15)) * (0.5 + 0.5*sev)

为什么用 `AND` 而不是只看价差：
    价格差本身很常见（促销、满减、渠道差异），单独看几乎没有信号。
    **"贵的那边还差评更多"才说明有问题**（可能是高价低质的渠道）。

业务类型：
    PRICE_UP_QUALITY_DOWN  dev > 0 且 sev 高 → 高价平台差评更多（贵且差）
    PRICE_DOWN_SUSPECT     dev < 0          → 低价平台，疑似渠道/货品问题
"""
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from uuid import uuid4

from hub.persistence.repo_price import PriceRepo
from hub.persistence.repository import RunRepo

MIN_ABS_DEV = 0.15      # 价格偏离阈值（≈ 16% 的价差）
MIN_SEV = 0.70          # 差评率分位阈值（前 30%）
MIN_REVIEWS = 20        # 评价数少于此的商品不参与（避免小样本噪声）


@dataclass
class PriceStats:
    """一次价格背离检测的统计。"""
    spus: int = 0           # 有跨平台价格数据的商品数
    flagged: int = 0        # 检出异常数
    wrote: int = 0          # 写入 anomaly 的行数
    trace_id: str = ""
    elapsed_s: float = 0.0


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def run_price_divergence(
    *,
    min_abs_dev: float = MIN_ABS_DEV,
    min_sev: float = MIN_SEV,
) -> PriceStats:
    """跑一次价格背离检测，结果写 anomaly(type='price')。"""
    t0 = time.time()
    repo = PriceRepo()
    rows = repo.fetch_stats()
    sku_map = repo.fetch_sku_map()

    stats = PriceStats()
    if not rows or not sku_map:
        print("没有价格数据或同款映射（先跑 hub align）")
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    # ---------- ① 平台内差评率 + 分位 ----------
    rates: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r.spu_id and (r.reviews or 0) >= MIN_REVIEWS:
            rates[r.platform][r.spu_id] = (r.bad or 0) / r.reviews

    def percentile(platform: str, spu: str) -> float:
        """该商品在本平台差评率的分位（0~1）。"""
        vals = sorted(rates.get(platform, {}).values())
        if not vals or spu not in rates[platform]:
            return 0.0
        v = rates[platform][spu]
        return sum(1 for x in vals if x <= v) / len(vals)

    # ---------- ② 按 canonical 归并两平台 ----------
    by_canonical: dict[str, dict] = defaultdict(dict)
    for r in rows:
        canonical = sku_map.get((r.platform, r.spu_id))
        if canonical:
            by_canonical[canonical][r.platform] = r

    # ---------- 创建 analysis_run ----------
    trace_id = uuid4().hex
    run_repo = RunRepo()
    run_id = run_repo.create(trace_id, batch_id=None)
    stats.trace_id = trace_id

    # ---------- ③ 逐个商品算背离 ----------
    anomaly_rows: list[dict] = []
    for canonical, per_platform in sorted(by_canonical.items()):
        if len(per_platform) < 2:
            continue                                    # 只有单平台，没法比

        prices = {p: float(r.price_ref) for p, r in per_platform.items()
                  if r.price_ref is not None}
        if len(prices) < 2:
            continue

        stats.spus += 1
        median = sum(prices.values()) / len(prices)
        devs = {p: math.log(v / median) for p, v in prices.items()}
        sevs = {p: percentile(p, per_platform[p].spu_id) for p in prices}

        # 贵的那一边
        expensive = max(devs, key=lambda p: devs[p])
        dev = devs[expensive]
        sev = sevs[expensive]

        if abs(dev) < min_abs_dev or sev < min_sev:
            continue                                    # 不满足"价差大 + 差评多"

        score = 100 * _sigmoid(4 * (abs(dev) - min_abs_dev)) * (0.5 + 0.5 * sev)
        kind = "PRICE_UP_QUALITY_DOWN" if dev > 0 else "PRICE_DOWN_SUSPECT"
        disparity = (max(prices.values()) / min(prices.values()) - 1) * 100

        anomaly_rows.append({
            "run_id": run_id,
            "type": "price",
            "canonical_spu_id": canonical,
            "metric": {
                "kind": kind,
                "score": round(score, 1),
                "dev": round(dev, 3),
                "disparity_pct": round(disparity, 1),
                "sev": round(sev, 2),
                "expensive_platform": expensive,
            },
            "evidence": {
                "title": per_platform[expensive].title,
                "prices": {p: round(v, 2) for p, v in prices.items()},
                "devs": {p: round(v, 3) for p, v in devs.items()},
                "review_rates": {p: round(rates.get(p, {}).get(per_platform[p].spu_id, 0.0), 3)
                                 for p in prices},
                "review_counts": {p: per_platform[p].reviews for p in prices},
            },
        })
        print(f"  💰 {per_platform[expensive].title[:20]}"
              f"｜{expensive} 贵 {disparity:.0f}%"
              f"｜差评率分位 {sev:.0%}｜score {score:.1f}｜{kind}")

    stats.flagged = len(anomaly_rows)
    stats.wrote = repo.insert_anomalies(anomaly_rows)
    stats.elapsed_s = round(time.time() - t0, 2)

    run_repo.update_status(trace_id, "DONE", stats={
        "module": "M4-price",
        "spus": stats.spus,
        "flagged": stats.flagged,
        "elapsed_s": stats.elapsed_s,
    })
    return stats
