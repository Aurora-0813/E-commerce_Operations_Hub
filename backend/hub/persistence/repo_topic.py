# -*- coding: utf-8 -*-
"""
评价主题读写（repo_topic.py）
============================
M3 归因链的数据访问层：取待分类的差评、写分类结果、按主题统计。

为什么单独一个 Repo：
    和 review_feature（M2）对称——一个模块一张派生表、一个 Repo，
    业务代码（analysis/）不写 SQL，也不碰 Session。

表关系（1:1）：
    record 1 ──── 1 review_topic
"""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hub.persistence.db import get_session
from hub.persistence.models import Record as RecordRow
from hub.persistence.models import ReviewTopic


class ReviewTopicRepo:
    """review_topic 表的读写。"""

    # ==================== 读：取待分类的差评 ====================

    def fetch_pending(self, *, rating_max: int = 2, force: bool = False,
                      limit: int | None = None):
        """取待分类的差评。

        筛选条件：
            rating <= rating_max   （差评阈值，默认 2）
            dqe_decision = 'keep'  （降噪后保留的；drop 的是水军/广告，不该归因）
            且没有 review_topic 记录（force=True 时忽略这条）

        :param rating_max: 差评阈值（业务可配，有的商家把 3 星也算差评）
        :param force:      True = 全量重算；False = 只处理没分类过的（增量）
        :param limit:      最多取多少条（抽检用）
        :return: Row 列表，字段：id / platform / rating / content_clean / platform_spu_id
        """
        with get_session() as s:
            stmt = (
                select(
                    RecordRow.id,
                    RecordRow.platform,
                    RecordRow.rating,
                    RecordRow.content_clean,
                    RecordRow.platform_spu_id,
                )
                .outerjoin(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
                .where(
                    RecordRow.rating <= rating_max,
                    RecordRow.dqe_decision == "keep",
                )
                .order_by(RecordRow.review_time.desc())
            )
            if not force:
                # 增量：只取还没分类的（LEFT JOIN 后为 NULL）
                stmt = stmt.where(ReviewTopic.record_id.is_(None))
            if limit:
                stmt = stmt.limit(limit)
            return s.execute(stmt).all()

    # ==================== 写：批量 upsert ====================

    def upsert_many(self, rows: list[dict]) -> int:
        """批量写入 review_topic（主键冲突则更新）。

        :param rows: 每个 dict 含 record_id / topic / severity / confidence /
                     impl / model_version / degraded
        :return: 写入了多少条
        """
        if not rows:
            return 0

        stmt = pg_insert(ReviewTopic).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["record_id"],
            set_={
                "topic": stmt.excluded.topic,
                "severity": stmt.excluded.severity,
                "confidence": stmt.excluded.confidence,
                "impl": stmt.excluded.impl,
                "model_version": stmt.excluded.model_version,
                "degraded": stmt.excluded.degraded,
            },
        )
        with get_session() as s:
            s.execute(stmt)
            s.commit()
        return len(rows)

    # ==================== 统计 ====================

    def count_by_topic_platform(self) -> list:
        """按 (topic, platform) 统计条数——归因的"格子"就是这个粒度。

        :return: Row 列表，字段 topic / platform / n，按 n 降序
        """
        with get_session() as s:
            stmt = (
                select(
                    ReviewTopic.topic,
                    RecordRow.platform,
                    func.count().label("n"),
                )
                .join(RecordRow, RecordRow.id == ReviewTopic.record_id)
                .group_by(ReviewTopic.topic, RecordRow.platform)
                .order_by(func.count().desc())
            )
            return s.execute(stmt).all()

    def count_by_category(self, topic: str, platform: str) -> list:
        """某个「主题 × 平台」格子内部的品类分布（归因的 stats 用）。

        record.platform_spu_id → spu_catalog.canonical_spu_id
        （同款对齐还没做，IngressRepo 暂时拿平台商品 ID 当 canonical_spu_id）

        :return: Row 列表，字段 category / n，按 n 降序
        """
        from hub.persistence.models import SpuCatalog

        with get_session() as s:
            stmt = (
                select(SpuCatalog.category_l1.label("category"),
                       func.count().label("n"))
                .join(RecordRow,
                      RecordRow.platform_spu_id == SpuCatalog.canonical_spu_id)
                .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
                .where(ReviewTopic.topic == topic, RecordRow.platform == platform)
                .group_by(SpuCatalog.category_l1)
                .order_by(func.count().desc())
            )
            return s.execute(stmt).all()

    def count_total(self) -> int:
        """已分类的差评总数。"""
        with get_session() as s:
            return s.scalar(select(func.count()).select_from(ReviewTopic)) or 0

    # ==================== 归因用：取一个格子的代表评价 ====================

    def fetch_cell_reviews(self, topic: str, platform: str, *, limit: int = 10,
                           min_chars: int = 20, max_chars: int = 300) -> list:
        """取某「主题 × 平台」格子的代表评价（喂给 LLM 做归因）。

        排序策略（业务依据：归因要"信息量"，不是"情绪强度"）：
            1. 先过滤掉过短（<20 字，没信息，如"太差了"）
              和过长（>300 字，可能跑题）的
            2. 按**长度降序**——长评价信息量大，适合当证据
            3. severity 作为次要排序

        如果过滤后不够 limit 条（比如这个格子都是短评），
        就放宽长度限制再取一次，保证有东西可归因。

        :return: Row 列表，字段 id / content_clean / severity
        """
        def _query(strict: bool):
            with get_session() as s:
                length = func.length(RecordRow.content_clean)
                stmt = (
                    select(RecordRow.id, RecordRow.content_clean, ReviewTopic.severity)
                    .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
                    .where(ReviewTopic.topic == topic,
                           RecordRow.platform == platform)
                )
                if strict:
                    stmt = stmt.where(length >= min_chars, length <= max_chars)
                stmt = (
                    stmt.order_by(length.desc(),
                                  ReviewTopic.severity.desc().nullslast())
                    .limit(limit)
                )
                return s.execute(stmt).all()

        rows = _query(strict=True)
        if len(rows) < limit:
            rows = _query(strict=False)
        return rows
