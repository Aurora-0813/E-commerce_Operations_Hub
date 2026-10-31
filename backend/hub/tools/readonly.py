# -*- coding: utf-8 -*-
"""
只读查询工具（readonly.py）—— 阶段 3.1
======================================
**给 agent 用的 5 个"数据层查询接口"**。

为什么单独建一个文件，而不是塞进 builtin.py：
    builtin.py 里是**写工具**（改描述/改价/发回复），它们的 handler 会改库、
    要回查、要补偿、要审批。
    这里的 5 个是**只读**（risk_level=L0），不改任何东西，
    `verify` 也是"再读一遍确认能读到"这种轻量检查 ✅
    → 两类工具的"危险程度"和"审查方式"完全不同，混在一起会让读者误判 ✅

设计原则（来自阶段 1.3 的实测，见 docs/开发指南/14_Agent规划能力实测.md）：
    **① 参数由代码注入，不让模型抄** —— 实测 7B 会把 36 位 UUID 抄错一位 ✅
    **② 返回值必须截断** —— 否则上下文很快撑爆
    **③ 每个返回值都带"来源"** —— 让下游能追溯到原始评价 ✅

当前已实现 5 个：`query_cluster` / `query_reviews` / `query_history` /
`query_product` / `query_outcome`（列名都核对过 `persistence/models.py`，不是猜的 ✅）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import (ClusterMember, IssueCluster, Record,
                                    ReviewTopic)
from hub.tools.spec import CallContext, ToolSpec, VerifyResult, register

# 返回值里"代表评价"最多几条（上下文预算：每条 ≤120 字，5 条 ≈ 600 字）
MAX_REVIEWS = 5
MAX_REVIEW_CHARS = 120


# ==================================================================== 参数模型
class QueryClusterArgs(BaseModel):
    cluster_id: str = Field(..., description="问题簇的 UUID（由调用方给出，不要自己编）")


class QueryReviewsArgs(BaseModel):
    cluster_id: str = Field(..., description="问题簇的 UUID")
    n: int = Field(MAX_REVIEWS, ge=1, le=MAX_REVIEWS, description="要几条代表评价")


class QueryHistoryArgs(BaseModel):
    topic: str = Field(..., description="问题类型，如 quality / size / logistics")
    tool: str | None = Field(None, description="只看某个工具的历史；不填=全部")


class QueryProductArgs(BaseModel):
    spu_id: str = Field(..., description="平台商品 ID（由调用方给出，不要自己编）")
    days: int = Field(30, ge=7, le=90, description="统计最近多少天的销售")


class QueryOutcomeArgs(BaseModel):
    proposal_id: str = Field(..., description="提案 UUID（由调用方给出，不要自己编）")


# ==================================================================== 公共工具
def _topic_of(s, cluster_id) -> str | None:
    """簇的"主导问题类型"—— 从成员评价的 `review_topic.topic` 取众数。

    为什么不能直接用 `issue_cluster.business_domain`：
        那一列存的是**商品品类**（女装/男装），是设计本意
        （`contracts/analysis.py:114` 的注释写着"如女装/连衣裙"）✅
        问题类型**没有单独存** —— 因为一个簇可能跨多个 topic（聚类按文本相似度）✅
    """
    rows = s.execute(
        select(ReviewTopic.topic, func.count())
        .join(ClusterMember, ClusterMember.record_id == ReviewTopic.record_id)
        .where(ClusterMember.cluster_id == cluster_id)
        .group_by(ReviewTopic.topic)
        .order_by(func.count().desc())
        .limit(1)
    ).all()
    return rows[0][0] if rows else None


def _ok(**detail) -> VerifyResult:
    """只读工具的 verify：它们不改状态，所以只需确认"真的读到了"。

    **不要**写成 `VerifyResult(True, {})` 了事 —— 那样 `verify` 就没有信息量了 ✅
    至少把"读到了哪一行/几条"记下来，`hub trace` 才看得出它真干了活 ✅
    """
    return VerifyResult(True, detail)


# ==================================================================== 工具 1
def _query_cluster(args: dict, ctx: CallContext) -> dict:
    """查一个簇的概况。"""
    from uuid import UUID
    cid = UUID(str(args["cluster_id"]))
    with get_session() as s:
        c = s.get(IssueCluster, cid)
        if c is None:
            raise ValueError(f"簇不存在：{cid}")
        topic = _topic_of(s, cid)
        # 平台：DB 里是 text[]，拼成 "taobao/jd"
        platforms = "/".join(c.platforms or []) or "未知"
        return {
            "cluster_id": str(c.id),
            "label": c.label,
            "review_count": c.review_count,
            "platforms": platforms,
            "priority_tier": c.priority_tier,
            "priority_score": float(c.priority_score) if c.priority_score is not None else None,
            "business_domain": c.business_domain,   # 品类（女装/男装）—— 保留，它有用
            "topic": topic,                          # 问题类型（现算的）
            "spu_count": len(c.spu_ids or []),
            # ⚠️ 也把**具体是哪些商品**返回（2026-10-31 补）：
            #    `spu_count` 只说"有几个"，而 reflect 环要拿一个**真的 spu_id** 去查销售 ✅
            #    （踩过：只有 count → reflect 拿不到 spu → 直接判"簇没有关联商品" ✅ ✗）
            #    截断到 10 个：一个簇可能覆盖几十个商品，全给会把 prompt 撑爆 ✅
            "spu_ids": list(c.spu_ids or [])[:10],
        }


# ==================================================================== 工具 2
def _query_reviews(args: dict, ctx: CallContext) -> dict:
    """查一个簇的代表评价（原文，已截断）。"""
    from uuid import UUID
    cid = UUID(str(args["cluster_id"]))
    n = int(args.get("n") or MAX_REVIEWS)
    with get_session() as s:
        rows = s.execute(
            select(Record.platform, Record.content_clean, ReviewTopic.topic)
            .join(ClusterMember, ClusterMember.record_id == Record.id)
            .outerjoin(ReviewTopic, ReviewTopic.record_id == Record.id)
            .where(ClusterMember.cluster_id == cid,
                   ClusterMember.is_representative.is_(True))
            .limit(n)
        ).all()
        reviews = [
            {
                "platform": p,
                "topic": t,
                "text": (txt or "")[:MAX_REVIEW_CHARS],
                "truncated": len(txt or "") > MAX_REVIEW_CHARS,
            }
            for p, txt, t in rows
        ]
        return {"cluster_id": str(cid), "count": len(reviews), "reviews": reviews}


# ==================================================================== 工具 3
def _query_history(args: dict, ctx: CallContext) -> dict:
    """查某类问题的历史战绩（复用 experience.history_for）。"""
    from hub.action.experience import history_for
    topic = str(args["topic"])
    only_tool = args.get("tool") or None
    records = history_for(topic)
    if only_tool:
        records = [r for r in records if r.tool == only_tool]
    # 截断：最多 5 条工具记录（一个 topic 下不会有太多工具）
    return {
        "topic": topic,
        "tools": [
            {"tool": r.tool, "positive": r.positive, "negative": r.negative,
             "inconclusive": r.inconclusive, "pending": r.pending,
             "summary": r.summary()}
            for r in records[:5]
        ],
        "total": len(records),
    }


# ==================================================================== 工具 4
def _query_product(args: dict, ctx: CallContext) -> dict:
    """查一个商品的概况 + 最近 N 天的销售聚合。

    列名核对过（`models.py:590-607` SalesDaily / `569-588` SpuCatalog）：
        canonical_spu_id / platform / stat_date / gmv / units / uv /
        conversion / refund_units / refund_amount / stock
    注意：`sales_daily` 的主键是 (spu_id, platform, stat_date) → 一个商品多平台会有多行。
    """
    from datetime import date, timedelta
    spu = str(args["spu_id"])
    days = int(args.get("days") or 30)
    since = date.today() - timedelta(days=days)
    with get_session() as s:
        from hub.persistence.models import SalesDaily, SpuCatalog
        cat = s.get(SpuCatalog, spu)
        agg = s.execute(
            select(
                SalesDaily.platform,
                func.sum(SalesDaily.gmv),
                func.sum(SalesDaily.units),
                func.sum(SalesDaily.uv),
                func.sum(SalesDaily.refund_units),
            )
            .where(SalesDaily.canonical_spu_id == spu, SalesDaily.stat_date >= since)
            .group_by(SalesDaily.platform)
        ).all()
        if cat is None and not agg:
            raise ValueError(f"商品不存在（spu_id={spu}）：catalog 和销售数据都没有")
        rows = [
            {
                "platform": p,
                "gmv": float(g or 0),
                "units": int(u or 0),
                "uv": int(v or 0),
                "refund_units": int(r or 0),
                # 退款率：分母用 units（件），口径和 M9 的 refund_rate 一致
                "refund_rate": round(float(r or 0) / int(u), 4) if u else None,
            }
            for p, g, u, v, r in agg
        ]
        return {
            "spu_id": spu,
            "title": getattr(cat, "title", None),
            "category": getattr(cat, "category_l1", None),
            "description_chars": len(getattr(cat, "description", "") or "") if cat else 0,
            "window_days": days,
            "platforms": rows,
        }


# ==================================================================== 工具 5
def _query_outcome(args: dict, ctx: CallContext) -> dict:
    """查一个提案的复盘结论（DID）。

    列名核对过（`models.py:663-687` ActionOutcome）：
        proposal_id / treat_canonical_spu_id / status / metrics_before /
        metrics_after / effect / conclusion / reason_code / observe_end
    """
    from uuid import UUID
    pid = UUID(str(args["proposal_id"]))
    with get_session() as s:
        from hub.persistence.models import ActionOutcome
        rows = s.scalars(
            select(ActionOutcome).where(ActionOutcome.proposal_id == pid)
        ).all()
        if not rows:
            raise ValueError(f"这个提案还没有复盘记录（proposal_id={pid}）")
        return {
            "proposal_id": str(pid),
            "outcomes": [
                {
                    "spu_id": r.treat_canonical_spu_id,
                    "status": r.status,
                    "conclusion": r.conclusion,
                    "reason_code": r.reason_code,
                    "observe_end": str(r.observe_end),
                    "effect": r.effect,             # DID + 置信区间（JSONB）
                }
                for r in rows[:5]                   # 一个提案可能影响多个商品
            ],
            "total": len(rows),
        }


# ==================================================================== 注册
def register_readonly() -> None:
    """注册 5 个只读工具（**幂等**：重复调用不报错）。

    为什么幂等：`hub` 的 CLI 每次调用都会走一遍注册，
    非幂等的话第二次调用就 `工具重复注册` 报错了 ✅
    """
    from hub.tools.spec import get as get_spec

    specs = [
        ToolSpec(
            name="query_cluster",
            version="v1",
            description="查一个簇的概况：名字、条数、平台、优先级、品类、问题类型",
            params_model=QueryClusterArgs,
            risk_level="L0",
            handler=_query_cluster,
            verify=lambda a, c, r: _ok(cluster_id=r.get("cluster_id"),
                                        label=r.get("label")),
            # ⚠️ **只读工具不能幂等缓存**（2026-10-31 踩过 ✅）
            #    `idempotent=True` 会让"同样的参数"直接返回**上次的结果** ✅
            #    → 只读查询会读到**旧数据** ✅ ✗
            #    （实测：MCP 第二次调 query_cluster 返回 REPLAYED，数据是几分钟前的）
            #    幂等是给"写"用的（防重复改价/重复发回复 ✅），
            #    给"读"用只会让运营看到过期的数字 ✅
            idempotent=False,
        ),
        ToolSpec(
            name="query_reviews",
            version="v1",
            description="查一个簇里的代表评价原文（最多 5 条，每条截断到 120 字）",
            params_model=QueryReviewsArgs,
            risk_level="L0",
            handler=_query_reviews,
            verify=lambda a, c, r: _ok(count=r.get("count")),
            idempotent=False,          # 只读：不缓存（见 query_cluster 的说明）
        ),
        ToolSpec(
            name="query_history",
            version="v1",
            description="查历史上同一类问题的处置方式和结果（有效/有害/不明确各几次）",
            params_model=QueryHistoryArgs,
            risk_level="L0",
            handler=_query_history,
            verify=lambda a, c, r: _ok(topic=r.get("topic"), tools=len(r.get("tools") or [])),
            idempotent=False,          # 只读：不缓存
        ),
        ToolSpec(
            name="query_product",
            version="v1",
            description="查一个商品的标题/品类/描述长度 + 最近 N 天的销售聚合（GMV/件数/退款率）",
            params_model=QueryProductArgs,
            risk_level="L0",
            handler=_query_product,
            verify=lambda a, c, r: _ok(spu_id=r.get("spu_id"),
                                        platforms=len(r.get("platforms") or [])),
            idempotent=False,          # 只读：缓存会因为"今天是新的一天"而算错 ✅
        ),
        ToolSpec(
            name="query_outcome",
            version="v1",
            description="查一个提案执行后的复盘结论（DID / 置信区间 / 结论 / 无法判定的原因）",
            params_model=QueryOutcomeArgs,
            risk_level="L0",
            handler=_query_outcome,
            verify=lambda a, c, r: _ok(proposal_id=r.get("proposal_id"),
                                        outcomes=r.get("total")),
            idempotent=False,          # 只读：复盘结论会变（WAITING → CONCLUDED）✅
        ),
    ]
    for spec in specs:
        if get_spec(spec.name) is None:
            register(spec)
