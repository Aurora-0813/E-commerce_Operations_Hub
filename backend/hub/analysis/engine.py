# -*- coding: utf-8 -*-
"""
M3 引擎（engine.py）
===================
一条命令跑完整个归因链：

    ① fetch_pending            取待分类差评（rating<=2 + DQE keep + 增量）
    ② classify_many            批量分类（LLM，三级降级）
    ③ upsert_many              写 review_topic
    ④ count_by_topic_platform  聚合出"格子"（topic × 平台）
    ⑤ attribute_cell           对每个格子（>=5 条）做归因
    ⑥ 写 analysis_run.stats    归因结果 + 统计快照

为什么归因要按"格子"做：
    笼统说"质量问题"没有行动价值；
    "淘宝的质量问题主要是缝线，京东主要是面料"才有。

为什么格子 < 5 条就跳过：
    证据不足时归因只能瞎编，宁可不给。
"""
import time
from dataclasses import dataclass, field
from uuid import uuid4

from hub.analysis.attribute import attribute_cell
from hub.analysis.classify import classify_many
from hub.llm import TextLLM, get_text_llm
from hub.persistence.repo_topic import ReviewTopicRepo
from hub.persistence.repository import RunRepo

# 一个格子至少多少条才值得归因
MIN_CELL_SIZE = 5
# 每个格子取多少条代表评价喂给 LLM
MAX_CELL_REVIEWS = 10


@dataclass
class TopicStats:
    """一次 M3 运行的统计。"""
    total: int = 0            # 待分类条数
    classified: int = 0       # 实际写入 review_topic 的条数
    batch_ok: int = 0         # 批量一次成功
    retry_ok: int = 0         # 逐条救回
    fallback: int = 0         # 关键词兜底（degraded）
    batches: int = 0          # 批量调用次数

    cells: int = 0            # 格子总数
    attributed: int = 0       # 成功归因的格子数
    skipped_cells: int = 0    # 跳过（样本不足）的格子数
    evidence_total: int = 0   # 归因证据总条数
    evidence_dropped: int = 0 # 被预过滤丢掉的编造证据数

    trace_id: str = ""
    elapsed_s: float = 0.0

    def summary(self) -> dict:
        return {
            "total": self.total,
            "classified": self.classified,
            "batch_ok": self.batch_ok,
            "retry_ok": self.retry_ok,
            "fallback": self.fallback,
            "batches": self.batches,
            "cells": self.cells,
            "attributed": self.attributed,
            "skipped_cells": self.skipped_cells,
            "evidence_total": self.evidence_total,
            "evidence_dropped": self.evidence_dropped,
            "trace_id": self.trace_id,
            "elapsed_s": self.elapsed_s,
        }


def run_topic_analysis(
    *,
    force: bool = False,
    limit: int | None = None,
    batch_size: int = 10,
    rating_max: int = 2,
    min_cell: int = MIN_CELL_SIZE,
    llm: TextLLM | None = None,
) -> TopicStats:
    """跑一次 M3 归因链。

    :param force:      True = 全量重算；False = 只处理没分类过的（增量）
    :param limit:      只处理前 N 条（抽检用）
    :param batch_size: 每批分类几条
    :param rating_max: 差评阈值
    :param min_cell:   格子最少多少条才归因
    :param llm:        自定义 LLM（测试用）
    """
    t0 = time.time()
    repo = ReviewTopicRepo()
    stats = TopicStats()

    llm = llm or get_text_llm()

    # ---------- ① 取待分类 ----------
    rows = repo.fetch_pending(rating_max=rating_max, force=force, limit=limit)
    stats.total = len(rows)

    if rows:
        print(f"待分类差评 {len(rows)} 条\n")

        # ---------- ② 分类 ----------
        items = [{"record_id": str(r.id), "text": r.content_clean} for r in rows]
        results, cstats = classify_many(
            llm, items, batch_size=batch_size, model_version=llm.name
        )
        stats.batch_ok = cstats.batch_ok
        stats.retry_ok = cstats.retry_ok
        stats.fallback = cstats.fallback
        stats.batches = cstats.batches

        # ---------- ③ 写库 ----------
        stats.classified = repo.upsert_many(results)
        print(f"\n分类完成：写入 {stats.classified} 条（兜底 {stats.fallback} 条）")
    else:
        # 分类结果已在库里（比如只想重跑归因）——跳过分类，直接归因
        print("没有待分类的差评（增量模式）——跳过分类，直接对已有结果做归因\n")

    # ---------- ④ 聚合（总是执行，这样"只重跑归因"也能工作）----------
    cells = repo.count_by_topic_platform()
    if not cells:
        print("review_topic 里没有数据，无法归因")
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    stats.cells = len(cells)
    total_classified = sum(c.n for c in cells) or 1
    print(f"聚合出 {stats.cells} 个格子（topic × 平台）")

    # ---------- ⑤ 逐格归因 ----------
    attributions: list[dict] = []
    cell_details: list[dict] = []

    for cell in cells:
        topic, platform, n = cell.topic, cell.platform, cell.n
        detail = {"topic": topic, "platform": platform, "count": n}

        if n < min_cell:
            stats.skipped_cells += 1
            detail.update(skipped=True, reason=f"样本不足（{n} < {min_cell}）")
            cell_details.append(detail)
            print(f"  ⏭  {topic}/{platform}（{n} 条）样本不足，跳过归因")
            continue

        # 取代表评价（按长度降序，severity 次之）
        reviews = repo.fetch_cell_reviews(topic, platform, limit=MAX_CELL_REVIEWS)
        payload = [{"review_id": str(r.id), "text": r.content_clean} for r in reviews]

        result = attribute_cell(llm, topic, platform, payload,
                                model_version=llm.name)
        if result is None:
            stats.skipped_cells += 1
            detail.update(skipped=True, reason="归因失败（模型输出不可用）")
            cell_details.append(detail)
            print(f"  ❌ {topic}/{platform}（{n} 条）归因失败")
            continue

        # 补上 SQL 算的统计（模型只说定性，数量我们算）
        cats = repo.count_by_category(topic, platform)
        result["stats"] = {
            "count": n,
            "share": round(n / total_classified, 3),
            "by_category": {(c.category or "未知"): c.n for c in cats},
        }

        attributions.append(result)
        stats.attributed += 1
        stats.evidence_total += len(result["evidence"])
        stats.evidence_dropped += result["dropped"]

        detail.update(
            skipped=False,
            confidence=result["confidence"],
            evidence=len(result["evidence"]),
            degraded=result["degraded"],
        )
        cell_details.append(detail)
        print(f"  ✅ {topic}/{platform}（{n} 条）归因完成，"
              f"证据 {len(result['evidence'])} 条，置信 {result['confidence']}")

    # ---------- ⑥ 写 analysis_run ----------
    trace_id = uuid4().hex
    stats.trace_id = trace_id
    stats.elapsed_s = round(time.time() - t0, 2)

    payload = {
        "module": "M3",
        "summary": stats.summary(),
        "attributions": attributions,
        "cells": cell_details,
        "by_topic": _group(cells, "topic"),
        "by_platform": _group(cells, "platform"),
    }

    run_repo = RunRepo()
    run_repo.create(trace_id, batch_id=None)      # 跨批次，batch_id 为空
    status = "DEGRADED" if stats.fallback else "DONE"
    run_repo.update_status(trace_id, status, stats=payload)

    return stats


def _group(cells, key: str) -> dict:
    """把格子按 topic 或 platform 汇总成 {名字: 条数}。"""
    out: dict[str, int] = {}
    for c in cells:
        name = getattr(c, key)
        out[name] = out.get(name, 0) + c.n
    return out
