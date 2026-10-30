# -*- coding: utf-8 -*-
"""
DQE 主流程（engine.py）
======================
把前面所有零件串起来：
    读 record 表 → 归一化 → 规则轨 → ML 轨 → 决策融合 → 回填 record 表

两种触发方式：
    - 独立跑：python -m hub.cli dqe [--force] [--batch <uuid>]
    - 流水线：pipeline/runner.py 里调用 run_dqe()

为什么按 batch_id 分组：
    R007（近似重复）是"同一批次内"的判断，
    必须一个批次一起看，不能跨批次比——否则会把不同时期的正常评价误判成重复。
"""
import time
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, select, update

from hub.contracts import DQEVerdict
from hub.core.tracing import traced
from hub.dqe.fusion import decide
from hub.dqe.ml import SpamModel
from hub.dqe.normalize import normalize
from hub.dqe.rules import RuleHit, check_item, find_near_duplicates, sort_hits
from hub.persistence.db import get_session
from hub.persistence.models import Record as RecordRow


@dataclass
class DqeStats:
    """一次 DQE 运行的统计。"""
    total: int = 0          # 处理了多少条
    keep: int = 0
    review: int = 0
    drop: int = 0
    engine: str = "rule_only"   # hybrid / rule_only
    degraded: bool = True       # 是否降级
    elapsed_s: float = 0.0      # 耗时


@traced("m1.dqe")
def run_dqe(
    *,
    batch_id: UUID | None = None,
    force: bool = False,
    use_ml: bool = True,
) -> DqeStats:
    """跑一次 DQE（核心函数）。

    :param batch_id: 只处理这个批次（None = 全部批次）
    :param force:    False = 只判 dqe_decision 为空的（增量）；
                     True  = 重判全部（换模型/调阈值后用）
    :param use_ml:   是否启用 ML（False = 强制纯规则，用于测试降级）
    """
    t0 = time.time()

    # 1. 取待判定的记录
    rows = _fetch_rows(batch_id, force)
    stats = DqeStats(total=len(rows))
    if not rows:
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    # 2. 加载模型（只加载一次，全批复用）
    model = SpamModel() if use_ml else None
    if model is not None and not model.degraded:
        stats.engine = "hybrid"
        stats.degraded = False
    else:
        stats.engine = "rule_only"
        stats.degraded = True

    # 3. 按批次分组（R007 只在同批次内比）
    by_batch: dict[UUID, list] = defaultdict(list)
    for r in rows:
        by_batch[r.batch_id].append(r)

    # 4. 逐批次判定
    verdicts: list[tuple[UUID, DQEVerdict]] = []
    for batch_rows in by_batch.values():
        texts = [normalize(r.content_clean) for r in batch_rows]

        # R007：批次内近似重复（保留首条）
        # 带上时间，只在"同一次刷屏"的时间窗内比较（默认 24h）
        times = [r.review_time for r in batch_rows]
        dup_flags = find_near_duplicates(texts, times)

        # ML：批量预测（降级时全 0）
        spam_scores = model.predict(texts) if model is not None else [0.0] * len(texts)

        # 逐条：规则 + R007 + 融合
        for i, row in enumerate(batch_rows):
            hits = check_item(texts[i], row.has_image)
            if dup_flags[i]:
                hits.append(RuleHit("R007", "近似重复"))
            hits = sort_hits(hits)

            v = decide(
                review_id=str(row.id),
                rating=row.rating,
                rule_hits=hits,
                spam_score=spam_scores[i],
                anomaly_score=0.0,      # 第一版不做异常检测
                engine=stats.engine,
                model_version=(model.version if model is not None and not model.degraded else None),
            )
            verdicts.append((row.id, v))

    # 5. 统计
    for _, v in verdicts:
        if v.decision == "keep":
            stats.keep += 1
        elif v.decision == "review":
            stats.review += 1
        else:
            stats.drop += 1

    # 6. 回填数据库
    _write_back(verdicts)

    stats.elapsed_s = round(time.time() - t0, 2)
    return stats


def _fetch_rows(batch_id: UUID | None, force: bool):
    """取待判定的记录（只取需要的列，避免加载整个 ORM 对象）。"""
    with get_session() as s:
        stmt = select(
            RecordRow.id,
            RecordRow.batch_id,
            RecordRow.content_clean,
            RecordRow.rating,
            RecordRow.has_image,
            RecordRow.review_time,
        ).where(RecordRow.data_type == "review")

        if batch_id is not None:
            stmt = stmt.where(RecordRow.batch_id == batch_id)
        if not force:
            # 增量：只判还没判过的
            stmt = stmt.where(RecordRow.dqe_decision.is_(None))

        return s.execute(stmt).all()


def _write_back(verdicts: list[tuple[UUID, DQEVerdict]]) -> None:
    """批量回填（Core executemany）。

    为什么用 RecordRow.__table__ 而不是 RecordRow：
        update(ORM类) 会触发 SQLAlchemy 的 "ORM bulk update by primary key" 机制，
        它要求每个参数里带主键值、且不支持带 WHERE 的 executemany，很容易报错。
        直接对底层 Table 操作、用底层 connection 执行，就是纯 Core 的 executemany，
        没有这些 ORM 限制。
    """
    if not verdicts:
        return

    tbl = RecordRow.__table__
    params = [
        {
            "pk": rid,
            "decision": v.decision,
            "reason": v.reason_code,
            "spam": float(v.spam_score),
            "anomaly": float(v.anomaly_score),
            "engine": v.engine,
            "version": v.model_version,
        }
        for rid, v in verdicts
    ]

    stmt = (
        update(tbl)
        .where(tbl.c.id == bindparam("pk"))
        .values(
            dqe_decision=bindparam("decision"),
            dqe_reason_code=bindparam("reason"),
            spam_score=bindparam("spam"),
            anomaly_score=bindparam("anomaly"),
            dqe_engine=bindparam("engine"),
            model_version=bindparam("version"),
        )
    )

    with get_session() as s:
        s.connection().execute(stmt, params)   # 纯 Core executemany
        s.commit()
