# -*- coding: utf-8 -*-
"""
表定义（models.py）—— 全量 34 张表
=================================

本文件是数据库的"唯一真相"：用 SQLAlchemy 2.0 声明式写法定义所有表。
建表不靠手动写 SQL，而是用 Alembic 从这些模型自动生成迁移（alembic revision --autogenerate）。

表按业务域分组（共 35 张）：
    一、数据接入与质量    ingest_batch / record / review_feature / review_topic / sku_mapping / price_snapshot
    二、运行与分析        analysis_run / issue_cluster / anomaly
    三、执行              action_proposal / tool_execution / idempotency_record / hitl_task / dead_letter
    四、可观测与评估      trace_span / judge_score / model_registry / eval_case / eval_result
    五、业务数据面        spu_catalog / sales_daily / incremental_cursor / platform_field_map / promo_calendar
    六、效果复盘          action_outcome
    七、回复与申诉        review_reply_draft / review_appeal
    八、协同与报表        notification / report_job / report_field_whitelist
    九、账号/租户/审计/知识  tenant / app_user / audit_log / knowledge_entry / case_entry

命名说明：
    - 原设计文档的 review 表改名为 record（系统支持评价/商品/订单/销售多种数据，用 data_type 区分）
    - 主键 UUID 用 gen_random_uuid()（PostgreSQL 13+ 内置）
    - 时间用 TIMESTAMPTZ（带时区），默认 now()
    - 结构化字段用 JSONB
    - case_entry.embedding 用 FLOAT8[]（ARRAY(Float)）代替 pgvector，避免额外扩展依赖
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有表的基类。Alembic 建表时用它拿到全部表定义。"""
    pass


# ==================== 一、数据接入与质量 ====================

class IngestBatch(Base):
    """导入批次：一次文件导入 = 一条记录。"""
    __tablename__ = "ingest_batch"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)          # 租户
    platform: Mapped[str] = mapped_column(Text, nullable=False)           # taobao / jd
    file_name: Mapped[str] = mapped_column(Text, nullable=False)          # 原始文件名
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)  # 文件指纹（幂等）
    row_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    row_kept: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    row_dropped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", "file_sha256", name="uq_batch_hash"),
    )


class Record(Base):
    """归一化后的业务记录（评价/商品/订单/销售通用）。

    原设计文档叫 review，现在叫 record：
    系统支持多种数据类型，用 data_type 区分，避免每类数据建一张表。
    """
    __tablename__ = "record"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("ingest_batch.id", ondelete="CASCADE"),
                                           nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)          # review/product/order/sales
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    platform_review_id: Mapped[str] = mapped_column(Text, nullable=False)  # 平台侧主键
    platform_spu_id: Mapped[str] = mapped_column(Text, nullable=False)    # 平台商品 ID
    canonical_spu_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # 对齐后（空=未映射）
    sku_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_clean: Mapped[str] = mapped_column(Text, nullable=False)      # 脱敏后文本
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    image_urls: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)   # 图片地址列表（最多 9 张）
    review_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dqe_decision: Mapped[str | None] = mapped_column(Text, nullable=True)       # 导入时为 NULL，DQE 回填
    dqe_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)   # 导入时为 NULL，DQE 回填
    spam_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    anomaly_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    dqe_engine: Mapped[str | None] = mapped_column(Text, nullable=True)        # 导入时为 NULL，DQE 回填
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("platform", "platform_review_id", name="uq_record_platform_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_record_rating"),
        CheckConstraint("dqe_decision IN ('keep','drop','review')", name="ck_record_decision"),
        CheckConstraint("dqe_engine IN ('ml+rule','rule_only')", name="ck_record_engine"),
        Index("idx_record_batch", "batch_id", "dqe_decision"),
        Index("idx_record_spu", "canonical_spu_id", "review_time"),
    )


class ReviewFeature(Base):
    """多模态特征：只存标签与哈希，无原图（阅后即焚）。"""
    __tablename__ = "review_feature"

    review_id: Mapped[UUID] = mapped_column(ForeignKey("record.id", ondelete="CASCADE"),
                                            primary_key=True)
    defect_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    color_deviation: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    vision_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    phash_bit: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vision_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class ReviewTopic(Base):
    """评价的文本主题（M3 归因链产出）。

    和 review_feature 是**姊妹表**：
        review_feature  —— M2 产出（图片特征，只有"有图"的评价才有）
        review_topic    —— M3 产出（文本分类，所有差评都有）

    为什么独立成表，而不是给 record 加列：
        1. **生产者不同**（M2 / M3）→ 可以独立失败、独立重跑；
        2. **基数不同**（89 条 vs 366 条）→ 合并会产生大量 NULL；
        3. **生命周期不同**（图片跑一次稳定，改提示词就要全量重算）。
    """
    __tablename__ = "review_topic"

    record_id: Mapped[UUID] = mapped_column(ForeignKey("record.id", ondelete="CASCADE"),
                                            primary_key=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)         # 主问题（封闭枚举）
    # 次要问题（0~1 个）：真实差评常一句话说多个问题，
    # 主标签只保留最主要的，其余放这里。统计"提到某问题多少条"时：
    #   WHERE topic = 'size' OR 'size' = ANY(secondary_topics)
    secondary_topics: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)   # 1~5（注意：review_feature 是 0~3）
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    impl: Mapped[str] = mapped_column(Text, nullable=False)          # llm.v1 / rule_fallback
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 提示词版本 = 模板内容的 SHA256 前 8 位。
    # 为什么要记：改提示词后要能区分"这批结果用的哪版"，否则准确率变化无法归因。
    prompt_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        # 主题用 DB 层约束锁死，防止代码里写错枚举值
        CheckConstraint(
            "topic IN ('quality','size','logistics','service','desc_mismatch','misship','other')",
            name="ck_review_topic_topic",
        ),
        CheckConstraint("severity IS NULL OR (severity BETWEEN 1 AND 5)",
                        name="ck_review_topic_severity"),
    )


class SkuMapping(Base):
    """跨平台 SPU 主数据对齐（同款识别）。"""
    __tablename__ = "sku_mapping"

    canonical_spu_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    platform_spu_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False,
                                                server_default=text("1.0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (Index("idx_sku_map_canonical", "canonical_spu_id"),)


class PriceSnapshot(Base):
    """价格快照：价格背离检测的数据源。"""
    __tablename__ = "price_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_spu_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'CNY'"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("canonical_spu_id", "platform", "captured_at", name="uq_price_snapshot"),
        CheckConstraint("price > 0", name="ck_price_positive"),
    )


# ==================== 二、运行与分析 ====================

class AnalysisRun(Base):
    """一次分析运行（run / trace_id）。"""
    __tablename__ = "analysis_run"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 可空：一次运行可能跨多个批次（比如 M3 归因链同时分析淘宝 + 京东的差评，
    # 填任何单一 batch_id 都不准确）
    batch_id: Mapped[UUID | None] = mapped_column(ForeignKey("ingest_batch.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'QUEUED'"))
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class PipelineRun(Base):
    """一条分析链的运行记录（M7 调度）。

    **为什么需要这张表（而不是让 Redis 记进度）**：
        队列是"至少一次"投递，消息可能重复、可能丢（Redis 重启）。
        如果"链跑到第几步"只存在消息里，一旦 worker 在
        `XADD 下一步` 和 `XACK 当前任务` 之间崩溃，链就断了，而且没人知道。

    这里的做法是：**数据库记进度，队列只负责"该干活了"这个信号**。
        · 每跑完一个阶段 → UPDATE current_stage（原子）
        · 崩溃 → 任务没 ACK → 被 XAUTOCLAIM 抢过来 → 从 current_stage 续跑
        · Redis 整个挂掉 → 进度还在，恢复后重新入队即可

    ⚠️ `(kind)` 上有**部分唯一索引**（`status IN ('QUEUED','RUNNING')` 时唯一）：
        防止手滑点两次 `hub enqueue` 导致两条链同时抢同一块 GPU。
        由数据库保证原子性——代码里"先查再插"有竞态，挡不住并发。
    """
    __tablename__ = "pipeline_run"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)      # full / backfill
    status: Mapped[str] = mapped_column(Text, nullable=False,
                                        server_default=text("'QUEUED'"))
    # 断点：最后**跑完**的阶段名；下一次从它的下一个开始
    current_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # 有阶段是降级完成的（LLM 抖动/兜底），链继续跑但要记一笔
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','DONE','DEGRADED','FAILED','DEAD')",
            name="ck_pipeline_run_status"),
        # 部分唯一索引：同一 kind 同时只能有一条"未结束"的链
        Index("uq_pipeline_run_active", "kind", unique=True,
              postgresql_where=text("status IN ('QUEUED','RUNNING')")),
    )


class IssueCluster(Base):
    """问题簇：多条评价聚成的同类问题。"""
    __tablename__ = "issue_cluster"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"),
                                         nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    business_domain: Mapped[str] = mapped_column(Text, nullable=False)
    platforms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False)
    growth_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    severity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    priority_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    priority_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    representative_review_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default=text("'{}'"))
    # 这个簇涉及哪些商品（平台商品 ID）。优先级打分要算"受影响销量占比"，
    # 没有它就只能退回"评价量占比"。
    spu_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("priority_tier IN ('P0','P1','P2','P3')", name="ck_cluster_tier"),
    )


class ClusterMember(Base):
    """簇的成员（issue_cluster 1 ── N record）。

    为什么需要：
        `issue_cluster` 只存了 5 条代表评价 + `spu_ids`，完整成员聚完就丢了。
        但下游都要用它：
            · M6「回复这一簇的全部差评」需要全部 review_id
            · M9「这个簇的差评有没有减少」需要按时间统计成员
            · M10「展开看这个簇的所有差评」需要列表
        所以必须落库，不能只在内存里。

    `is_representative`：距质心最近的那 5 条（用于前端摘要展示）。
    """
    __tablename__ = "cluster_member"

    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue_cluster.id", ondelete="CASCADE"), primary_key=True)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey("record.id", ondelete="CASCADE"), primary_key=True)
    is_representative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class Anomaly(Base):
    """业务异常：价格背离 / 销量波动 / 无效订单。"""
    __tablename__ = "anomaly"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"),
                                         nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_spu_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=text("now()"))


# ==================== 三、执行 ====================

class ActionProposal(Base):
    """动作提案：LLM/规则建议做的一个动作。"""
    __tablename__ = "action_proposal"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"),
                                         nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_version: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    # 依据引用：哪个簇 / 哪个异常 / 哪几条评价。**只放依据，不放别的。**
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                server_default=text("'[]'::jsonb"))
    # 为什么这么建议（LLM 写的一句人话）。
    # 为什么不放 args：args 是"工具要的参数"，会被 params_model 强校验（extra=forbid）；
    # 为什么不放 evidence_refs：那个字段的语义是"依据是谁"，塞理由进去就是名不副实。
    # 为什么要存：这句话是模型生成的，事后重算不出来（M10 要展示给运营看）。
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PROPOSED'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("risk_level IN ('L0','L1','L2','L3')", name="ck_proposal_risk"),
    )


class ToolExecution(Base):
    """工具执行记录（含被拒绝的调用）。"""
    __tablename__ = "tool_execution"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_proposal.id", ondelete="CASCADE"), nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    deny_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    verify_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    """幂等记录：Redis 之外的持久化兜底。"""
    __tablename__ = "idempotency_record"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    result_ref: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HitlTask(Base):
    """人工介入任务（L3 高风险动作转人工）。"""
    __tablename__ = "hitl_task"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_proposal.id", ondelete="CASCADE"), nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    assignee: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class DeadLetter(Base):
    """死信队列（重试超限的任务）。"""
    __tablename__ = "dead_letter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==================== 四、可观测与评估 ====================

class TraceSpan(Base):
    """链路追踪片段。

    注意：设计文档原计划按月分区，这里先建普通表（开发期够用），
    上线前再改成分区表（分区需要手动创建 monthly partition）。
    """
    __tablename__ = "trace_span"

    trace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    span_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class JudgeScore(Base):
    """线上 Judge 打分（LLM as a Judge）。"""
    __tablename__ = "judge_score"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    span_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    sampled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class ModelRegistry(Base):
    """模型版本登记（水军分类器 / 孤立森林 / 向量模型）。"""
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=text("now()"))

    __table_args__ = (UniqueConstraint("kind", "version", name="uq_model_kind_version"),)


class EvalCase(Base):
    """评测用例（唯一权威来源是 YAML，此表由 hubctl eval sync 同步）。"""
    __tablename__ = "eval_case"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    gate_type: Mapped[str] = mapped_column(Text, nullable=False)
    fixture_path: Mapped[str] = mapped_column(Text, nullable=False)
    assertion: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        CheckConstraint("gate_type IN ('hard','soft')", name="ck_eval_gate"),
    )


class EvalResult(Base):
    """评测结果。"""
    __tablename__ = "eval_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("eval_case.id"), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


# ==================== 五、业务数据面（M11） ====================

class SpuCatalog(Base):
    """商品目录（主数据）。"""
    __tablename__ = "spu_catalog"

    canonical_spu_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # 注意：这一列现在存的是**平台商品 ID**（TB-0001 / JD-0001），不是真正的 canonical。
    # 同款对齐由 sku_mapping 负责；跨平台查询用 JOIN sku_mapping。
    # 补 platform 列是为了能区分"哪一行是哪个平台的商品"。
    platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 详情页描述。M5 的 listing.update_desc 工具改这一列、回查也读这一列。
    # 真实场景里描述是"平台 × 版本"的（草稿/审核/上线），这里只存当前生效版本。
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_l1: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_l2: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_ref: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ON_SALE'"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class SalesDaily(Base):
    """日粒度销售聚合（只存聚合，不存订单明细，无 PII）。"""
    __tablename__ = "sales_daily"

    canonical_spu_id: Mapped[str] = mapped_column(Text, primary_key=True)
    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    gmv: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    uv: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    conversion: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    refund_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False,
                                                   server_default=text("0"))
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=text("now()"))

    __table_args__ = (
        Index("idx_sales_date", "stat_date"),
        CheckConstraint("conversion IS NULL OR (conversion >= 0 AND conversion <= 1)",
                        name="ck_sales_conversion"),
    )


class IncrementalCursor(Base):
    """增量拉取水位（断点续传）。"""
    __tablename__ = "incremental_cursor"

    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    entity: Mapped[str] = mapped_column(Text, primary_key=True)
    watermark_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'IDLE'"))


class PlatformFieldMap(Base):
    """平台字段映射（配置化，新增平台只加数据）。"""
    __tablename__ = "platform_field_map"

    platform: Mapped[str] = mapped_column(Text, primary_key=True)
    source_column: Mapped[str] = mapped_column(Text, primary_key=True)
    target_field: Mapped[str] = mapped_column(Text, nullable=False)
    transform: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'identity'"))
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PromoCalendar(Base):
    """大促日历（复盘的干扰排除）。"""
    __tablename__ = "promo_calendar"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    platforms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                                 server_default=text("'{}'"))
    # 促销覆盖的商品（空数组 = 全店大促）
    #
    # ⚠️ 这一列决定了"大促算不算混杂"，是 M9 判结论的关键：
    #     全店大促（空数组）→ 处理组和对照组都在里面 → DID 能减掉 → **不是混杂**
    #     定向促销（有商品）→ 只有处理组的商品在做活动 → 对照组减不掉 → **是混杂**
    #   真实业务里"主推款才做大促"非常常见，所以这个区分必须有。
    #   没有这一列的话，一个全店大促会让所有动作都被判成"撞大促"，M9 直接全废。
    spu_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                               server_default=text("'{}'"))


# ==================== 六、效果复盘（M9） ====================

class ActionOutcome(Base):
    """动作的复盘结果（DID）。"""
    __tablename__ = "action_outcome"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_proposal.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    treat_canonical_spu_id: Mapped[str] = mapped_column(Text, nullable=False)
    control_spu_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                                       server_default=text("'{}'"))
    baseline_start: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_end: Mapped[date] = mapped_column(Date, nullable=False)
    observe_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'WAITING'"))
    metrics_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics_after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    effect: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        # 一个动作可能影响**多个商品**（回复一个簇 → 覆盖几十个商品），
        # 所以唯一性应该是"同一提案下，同一商品只有一条复盘"，
        # 而不是"一个提案只有一条"——原来那个约束写的时候，
        # 提案还都是"改一个商品的价格"，没考虑到簇级动作。
        #
        # 为什么坚持"一个商品一条"：DID 的单位是【处理商品 vs 对照商品】的配对，
        # 一条记录对多个商品就没法配对，也回答不了"对哪个商品更有效"。
        UniqueConstraint("proposal_id", "treat_canonical_spu_id",
                         name="uq_outcome_proposal_spu"),
        CheckConstraint("conclusion IN ('POSITIVE','NEGATIVE','NEUTRAL','INCONCLUSIVE')",
                        name="ck_outcome_conclusion"),
        Index("idx_outcome_due", "status", "observe_end"),
    )


# ==================== 七、回复与申诉（M10） ====================

class ReviewReplyDraft(Base):
    """回复草稿（生成→合规→审批→发送）。"""
    __tablename__ = "review_reply_draft"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    cluster_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue_cluster.id", ondelete="CASCADE"), nullable=True)
    review_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PGUUID(as_uuid=True)), nullable=False)
    template_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    compliance_flags: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                   server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    reviewer: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_reply_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class ReviewAppeal(Base):
    """差评申诉。"""
    __tablename__ = "review_appeal"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    review_id: Mapped[UUID] = mapped_column(ForeignKey("record.id"), nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    reason_type: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


# ==================== 八、协同与报表（M10） ====================

class Notification(Base):
    """通知 outbox（幂等，防风暴）。"""
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("event_type", "resource_id", "channel", "recipient",
                         name="uq_notification_idem"),
    )


class ReportJob(Base):
    """报表生成任务。"""
    __tablename__ = "report_job"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'QUEUED'"))
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("format IN ('pdf','xlsx','html')", name="ck_report_format"),
    )


class ReportFieldWhitelist(Base):
    """报表字段白名单（只允许聚合值，原文/PII 永不入内）。"""
    __tablename__ = "report_field_whitelist"

    field_name: Mapped[str] = mapped_column(Text, primary_key=True)
    agg_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


# ==================== 九、账号 / 租户 / 审计 / 知识（前端阶段） ====================

class Tenant(Base):
    """租户（一个商家 = 一个租户）。"""
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class AppUser(Base):
    """平台用户。"""
    __tablename__ = "app_user"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    sso_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_email"),
        CheckConstraint("role IN ('operator','supervisor','admin','analyst')", name="ck_user_role"),
    )


class AuditLog(Base):
    """审计日志（append-only，收回 UPDATE/DELETE 授权）。"""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (Index("idx_audit_time", "created_at"),)


class KnowledgeEntry(Base):
    """知识条目（playbook / faq / 规则候选 / prompt 补丁）。"""
    __tablename__ = "knowledge_entry"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("type IN ('playbook','faq','rule_candidate','prompt_patch')",
                        name="ck_knowledge_type"),
    )


class CaseEntry(Base):
    """案例库（Case RAG，只收录 POSITIVE/NEGATIVE）。"""
    __tablename__ = "case_entry"

    case_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                         server_default=text("gen_random_uuid()"))
    cluster_label: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    business_domain: Mapped[str] = mapped_column(Text, nullable=False)
    platforms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                                 server_default=text("'{}'"))
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_conclusion: Mapped[str] = mapped_column(Text, nullable=False)
    effect_size: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    representative_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    source_outcome_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("outcome_conclusion IN ('POSITIVE','NEGATIVE')", name="ck_case_conclusion"),
        Index("idx_case_domain", "business_domain", "status"),
    )
