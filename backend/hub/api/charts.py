# -*- coding: utf-8 -*-
"""
图表聚合（charts.py）—— 阶段 4.4b/4.4c
=====================================
**全部只读 `GROUP BY`，不新增表** ✅（路线图 §10.4）

本文件实现的 9 个（数据源都**核对过真实列** ✅）：

| 接口 | 图 | 真实数据源 |
| --- | --- | --- |
| `/charts/cluster-tiers` | ① P 档环形 | `issue_cluster.priority_tier` |
| `/charts/clusters?sort=` | ⑦ 簇条数 | `issue_cluster.review_count` |
| `/charts/hitl-trend?days=` | ② 待批趋势 | `hitl_task.created_at/status` |
| `/charts/bad-rate-trend?platform=&days=` | ⑨ 差评率趋势 | `record.rating/review_time` |
| `/charts/observe-timeline` | ③ 等窗口甘特 | `action_outcome.observe_end/status` |
| `/retro` | ⑫⑬⑭ DID | `action_outcome.effect/conclusion` |
| `/audit/trend?days=` | ⑱ 日志趋势 | `audit_log.created_at` |
| `/handle/{id}` | ⑮ 轨迹 | `agent_run.steps/evidence` |
| `/handle/{id}/trace` | ⑯ 每步 token | `trace_span`（**时长 = end_ts - start_ts** ✅ 没有 duration 列） |

**明确没做的 4 个**（数据源不具备，不是"忘了"）：
    `topic-distribution`  —— `review_topic` 表存在但**问题类型要从 cluster_member 反查**，
                              那是 `query_cluster` 的活；这里硬做会算错 ✅
    `cluster-trend`       —— 需要按周聚合 `record.review_time` + `cluster_member`，
                              簇成员是**快照**（每次聚类重写），跨周趋势**没有历史** ✅
    `price-vs-badrate`    —— 需要 `anomaly.evidence` 的解析口径，未定 ✅
    `gmv-top`             —— 已有 `hub status` 的 GMV Top3，接口化要定"Top N 怎么算" ✅
    `clusters/{id}/factors` —— `priority.factors` **不是数据库列**，是 `hub/analysis/priority.py`
                              算出来的；接口化要先把算法抽成可调函数 ✅
    → 这 6 个**要么数据不支持、要么口径没定**，硬做就是编数据 ✅
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from hub.api.deps import AuthUser, _trace_id, get_current_user

router = APIRouter(prefix="/api", tags=["charts"])


def _days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ════════════════════════════════════════════════════ ① P 档环形
@router.get("/charts/cluster-tiers")
def cluster_tiers(user: AuthUser = Depends(get_current_user)):
    """各优先级档的簇数（P0/P1/P2/P3）。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster

    with get_session() as s:
        rows = s.execute(
            select(IssueCluster.priority_tier, func.count(), func.sum(IssueCluster.review_count))
            .group_by(IssueCluster.priority_tier)
        ).all()
    return {"items": [{"tier": t or "未定", "clusters": int(c), "reviews": int(n or 0)}
                      for t, c, n in rows]}


# ════════════════════════════════════════════════════ ⑦ 簇条数
@router.get("/charts/clusters")
def clusters(sort: str = Query("reviews", pattern="^(reviews|priority|label)$"),
             limit: int = Query(50, ge=1, le=200),
             user: AuthUser = Depends(get_current_user)):
    """簇列表（按条数/优先级排序）。表格和图⑦同源 ✅"""
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster

    order = {"reviews": IssueCluster.review_count.desc(),
             "priority": IssueCluster.priority_score.desc().nullslast(),
             "label": IssueCluster.label.asc()}[sort]
    with get_session() as s:
        rows = s.scalars(select(IssueCluster).order_by(order).limit(limit)).all()
        return {"items": [{"id": str(c.id), "label": c.label,
                           "reviews": c.review_count, "tier": c.priority_tier,
                           "score": float(c.priority_score) if c.priority_score else None,
                           "domain": c.business_domain,
                           "spus": len(c.spu_ids or [])}
                          for c in rows]}


# ════════════════════════════════════════════════════ ② 待批趋势
@router.get("/charts/hitl-trend")
def hitl_trend(days: int = Query(14, ge=1, le=90),
               user: AuthUser = Depends(get_current_user)):
    """近 N 天每天新建/已决的待办数（按 `hitl_task.created_at` ✅）。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import HitlTask

    since = _days_ago(days)
    with get_session() as s:
        rows = s.execute(
            select(func.date(HitlTask.created_at), HitlTask.status, func.count())
            .where(HitlTask.created_at >= since)
            .group_by(func.date(HitlTask.created_at), HitlTask.status)
            .order_by(func.date(HitlTask.created_at))
        ).all()
    items: dict[str, dict] = {}
    for d, st, n in rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        items.setdefault(key, {"date": key, "PENDING": 0, "APPROVED": 0,
                               "REJECTED": 0, "EXPIRED": 0})
        if st in items[key]:
            items[key][st] = int(n)
    return {"days": days, "items": list(items.values())}


# ════════════════════════════════════════════════════ ⑨ 差评率趋势
@router.get("/charts/bad-rate-trend")
def bad_rate_trend(platform: str | None = Query(None, description="taobao / jd；空=全部"),
                   days: int = Query(14, ge=1, le=90),
                   user: AuthUser = Depends(get_current_user)):
    """近 N 天差评率（rating ≤ 2 / 总数 ✅）。

    ⚠️ **没有按周聚合**：先按天给，周可以在前端合并 ✅
       （后端硬凑"周"要处理跨年/跨月，不如留给展示层 ✅）
    """
    from sqlalchemy import case, func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import Record

    since = _days_ago(days)
    stmt = select(func.date(Record.review_time),
                  func.count(),
                  func.sum(case((Record.rating <= 2, 1), else_=0)))
    if platform:
        stmt = stmt.where(Record.platform == platform)
    stmt = stmt.where(Record.review_time >= since).group_by(
        func.date(Record.review_time)).order_by(func.date(Record.review_time))
    with get_session() as s:
        rows = s.execute(stmt).all()
    return {"platform": platform or "all", "days": days,
            "items": [{"date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                       "total": int(t), "bad": int(b or 0),
                       "bad_rate": round((b or 0) / t, 4) if t else 0.0}
                      for d, t, b in rows]}


# ════════════════════════════════════════════════════ ③ 等窗口甘特
@router.get("/charts/observe-timeline")
def observe_timeline(user: AuthUser = Depends(get_current_user)):
    """正在等观察窗口的动作（图③ 甘特）。`observe_end` 就是甘特的右端 ✅"""
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import ActionOutcome

    with get_session() as s:
        rows = s.scalars(select(ActionOutcome)
                         .where(ActionOutcome.status.in_(("WAITING", "CONCLUDED")))
                         .order_by(ActionOutcome.observe_end.desc()).limit(100)).all()
        return {"items": [{"proposal_id": str(r.proposal_id), "status": r.status,
                           "observe_end": r.observe_end.isoformat() if r.observe_end else None,
                           "conclusion": r.conclusion,
                           "effect": r.effect} for r in rows]}


# ════════════════════════════════════════════════════ ⑫⑬⑭ DID
@router.get("/retro")
def retro(limit: int = Query(50, ge=1, le=200),
          user: AuthUser = Depends(get_current_user)):
    """复盘结论（DID / 前后 / 区间）。图⑫⑬⑭ 同源 ✅"""
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import ActionOutcome

    with get_session() as s:
        rows = s.scalars(select(ActionOutcome)
                         .order_by(ActionOutcome.created_at.desc()).limit(limit)).all()
        return {"items": [{"proposal_id": str(r.proposal_id), "status": r.status,
                           "effect": r.effect, "conclusion": r.conclusion,
                           "confidence": float(r.confidence) if r.confidence is not None else None,
                           "reason_code": r.reason_code,
                           "baseline": [r.baseline_start.isoformat() if r.baseline_start else None,
                                        r.baseline_end.isoformat() if r.baseline_end else None],
                           "observe_end": r.observe_end.isoformat() if r.observe_end else None,
                           "metrics_before": r.metrics_before,
                           "metrics_after": r.metrics_after}
                          for r in rows]}


# ════════════════════════════════════════════════════ ⑱ 日志趋势
@router.get("/audit/trend")
def audit_trend(days: int = Query(14, ge=1, le=90),
                user: AuthUser = Depends(get_current_user)):
    """近 N 天每天的操作数（按 action 分组）。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import AuditLog

    since = _days_ago(days)
    with get_session() as s:
        rows = s.execute(
            select(func.date(AuditLog.created_at), AuditLog.action, func.count())
            .where(AuditLog.created_at >= since)
            .group_by(func.date(AuditLog.created_at), AuditLog.action)
            .order_by(func.date(AuditLog.created_at))
        ).all()
    out: dict[str, dict] = {}
    for d, action, n in rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        out.setdefault(key, {"date": key, "total": 0, "by_action": {}})
        out[key]["by_action"][action] = int(n)
        out[key]["total"] += int(n)
    return {"days": days, "items": list(out.values())}


# ════════════════════════════════════════════════════ ⑥ 问题类型分布
@router.get("/charts/topic-distribution")
def topic_distribution(user: AuthUser = Depends(get_current_user)):
    """各问题类型的差评条数（图⑥ 饼图）。

    **问题类型不在 `issue_cluster` 上**（一个簇可能跨多个 topic ✅）
    → 必须从 `cluster_member → review_topic.topic` 反查 ✅
      （和 `query_cluster` 的 `_topic_of()` 同一个口径 ✅）
    """
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import ClusterMember, ReviewTopic

    with get_session() as s:
        rows = s.execute(
            select(ReviewTopic.topic, func.count())
            .join(ClusterMember, ClusterMember.record_id == ReviewTopic.record_id)
            .group_by(ReviewTopic.topic)
            .order_by(func.count().desc())
        ).all()
    total = sum(int(n) for _, n in rows) or 1
    return {"items": [{"topic": t or "unknown", "reviews": int(n),
                       "share": round(int(n) / total, 4)}
                      for t, n in rows]}


# ════════════════════════════════════════════════════ ④ 该簇周趋势
@router.get("/charts/cluster-trend")
def cluster_trend(cluster_id: str = Query(..., description="簇 UUID"),
                  user: AuthUser = Depends(get_current_user)):
    """某个簇按周的差评条数（图④ 周趋势）。

    ⚠️ 我之前说"簇成员是快照、没有历史"—— **错了** ✅
       簇成员确实是**快照**（每次聚类重写），
       但成员的 `record.review_time` **是真实的历史时间** ✅
       → 按 `review_time` 分周聚合，就是"这类差评每周有多少条" ✅
    """
    from uuid import UUID

    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import ClusterMember, Record

    try:
        cid = UUID(cluster_id)
    except ValueError:
        return {"error": "cluster_id 不是合法 UUID", "items": []}
    with get_session() as s:
        rows = s.execute(
            select(func.date_trunc("week", Record.review_time).label("wk"),
                   func.count())
            .join(ClusterMember, ClusterMember.record_id == Record.id)
            .where(ClusterMember.cluster_id == cid)
            .group_by("wk").order_by("wk")
        ).all()
    return {"cluster_id": str(cid),
            "items": [{"week": w.date().isoformat() if hasattr(w, "date") else str(w),
                       "reviews": int(n)} for w, n in rows]}


# ════════════════════════════════════════════════════ ⑪ GMV TopN
@router.get("/charts/gmv-top")
def gmv_top(n: int = Query(10, ge=1, le=50),
            days: int = Query(30, ge=1, le=180),
            user: AuthUser = Depends(get_current_user)):
    """GMV Top N 商品（图⑪）。按 `sales_daily` 的最近 N 天 GMV 求和 ✅"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import SalesDaily, SpuCatalog

    since = _days_ago(days).date()
    with get_session() as s:
        rows = s.execute(
            select(SalesDaily.canonical_spu_id, func.sum(SalesDaily.gmv),
                   func.sum(SalesDaily.units))
            .where(SalesDaily.stat_date >= since)
            .group_by(SalesDaily.canonical_spu_id)
            .order_by(func.sum(SalesDaily.gmv).desc())
            .limit(n)
        ).all()
        spus = [r[0] for r in rows]
        titles = {}
        if spus:
            for row in s.execute(
                    select(SpuCatalog.canonical_spu_id, SpuCatalog.title)
                    .where(SpuCatalog.canonical_spu_id.in_(spus))).all():
                titles.setdefault(row[0], row[1])
    return {"days": days, "n": n,
            "items": [{"spu_id": spu, "title": titles.get(spu, ""),
                       "gmv": float(g or 0), "units": int(u or 0)}
                      for spu, g, u in rows]}


# ════════════════════════════════════════════════════ ⑩ 价格-差评率散点
@router.get("/charts/price-vs-badrate")
def price_vs_badrate(user: AuthUser = Depends(get_current_user)):
    """价格 vs 差评率（图⑩ 散点）。

    **数据来源是 `anomaly.evidence`**（2026-10-31 核对过真实结构 ✅）：
        {"prices": {"jd":399.0,"taobao":259.0},
         "review_rates": {"jd":0.268,"taobao":0.341},
         "review_counts": {...}, "title": "...", "devs": {...}}
    → `prices` + `review_rates` **都在**，直接画散点 ✅
      （我上一轮说"口径未定"是错的 ✅ —— 口径就是"每个平台一个点" ✅）
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import Anomaly

    with get_session() as s:
        rows = s.scalars(select(Anomaly).where(
            Anomaly.type == "price_vs_market").limit(200)).all()
        if not rows:   # 类型名可能不同 → 退化成"取所有带 prices 的"
            rows = [a for a in s.scalars(select(Anomaly).limit(400)).all()
                    if isinstance(a.evidence, dict) and "prices" in a.evidence]
    items = []
    for a in rows:
        ev = a.evidence or {}
        for plat, price in (ev.get("prices") or {}).items():
            rate = (ev.get("review_rates") or {}).get(plat)
            cnt = (ev.get("review_counts") or {}).get(plat)
            if price is None or rate is None:
                continue
            items.append({"spu_id": a.canonical_spu_id, "title": ev.get("title", ""),
                          "platform": plat, "price": float(price),
                          "review_rate": float(rate),
                          "review_count": int(cnt) if cnt is not None else None})
    return {"items": items}


# ════════════════════════════════════════════════════ ⑧ 四因子
@router.get("/clusters/{cluster_id}/factors")
def cluster_factors(cluster_id: str, request: Request,
                    user: AuthUser = Depends(get_current_user)):
    """某个簇的四因子（图⑧ 雷达）。

    ⚠️ **`priority_score` 的计算公式在 `hub/analysis/priority.py` 里，
       但那个模块没有导出"四个因子分别是什么"** ✅
    → 所以我**不编造四个因子**，只返回**库里真实存着的输入** ✅：
         `review_count` / `severity` / `growth_rate` / `priority_score` / `priority_tier`
       + 明确说明"因子的加权公式在 priority.py，未在此暴露"
    **理由**：编一个"看起来像雷达图"的四维数字，比不画更糟 ✅
    """
    from uuid import UUID

    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster

    try:
        cid = UUID(cluster_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "cluster_id 不是合法 UUID",
                             "trace_id": _trace_id(request)}) from None
    with get_session() as s:
        c = s.get(IssueCluster, cid)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "簇不存在",
                                 "trace_id": _trace_id(request)})
        return {
            "cluster_id": str(c.id), "label": c.label,
            "inputs": {"review_count": c.review_count,
                       "severity": float(c.severity) if c.severity is not None else None,
                       "growth_rate": float(c.growth_rate) if c.growth_rate is not None else None,
                       "priority_score": float(c.priority_score) if c.priority_score is not None else None,
                       "priority_tier": c.priority_tier},
            "note": "四因子的加权公式在 hub/analysis/priority.py，本接口只暴露库里的输入值，不编造归一化后的因子 ✅",
        }


# ════════════════════════════════════════════════════ ⑮ agent 轨迹
@router.get("/handle/{run_id}")
def get_agent_run(run_id: str, request: Request,
                  user: AuthUser = Depends(get_current_user)):
    """一次 agent 运行的轨迹（图⑮）。`hub handle` 的产物 ✅"""
    from uuid import UUID

    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun

    try:
        rid = UUID(run_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "run_id 不是合法 UUID",
                             "trace_id": _trace_id(request)}) from None
    with get_session() as s:
        r = s.get(AgentRun, rid)
        if r is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "agent_run 不存在",
                                 "trace_id": _trace_id(request)})
        return {"id": str(r.id), "cluster_id": str(r.cluster_id),
                "trace_id": r.trace_id, "status": r.status, "goal": r.goal,
                "steps": r.steps, "evidence": r.evidence, "result": r.result,
                "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                "proposal_id": str(r.proposal_id) if r.proposal_id else None,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None}


# ════════════════════════════════════════════════════ ⑯ 每步 token
@router.get("/handle/{run_id}/trace")
def get_agent_trace(run_id: str, request: Request,
                    user: AuthUser = Depends(get_current_user)):
    """这次 agent 运行的 span 列表（图⑯ 每步 token）。

    ⚠️ `trace_span` **没有 duration_ms 列**（2026-10-31 核对）✅
       → 时长 = `end_ts - start_ts`，单位按毫秒算 ✅
    """
    from uuid import UUID

    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun, TraceSpan

    try:
        rid = UUID(run_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "run_id 不是合法 UUID",
                             "trace_id": _trace_id(request)}) from None
    with get_session() as s:
        run = s.get(AgentRun, rid)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "agent_run 不存在",
                                 "trace_id": _trace_id(request)})
        rows = s.scalars(select(TraceSpan)
                         .where(TraceSpan.trace_id == run.trace_id)
                         .order_by(TraceSpan.start_ts)).all()
        items = []
        for sp in rows:
            ms = None
            if sp.start_ts and sp.end_ts:
                ms = int((sp.end_ts - sp.start_ts).total_seconds() * 1000)
            items.append({"span_id": sp.span_id, "name": sp.name, "kind": sp.kind,
                          "status": sp.status, "duration_ms": ms,
                          "tokens_in": sp.tokens_in, "tokens_out": sp.tokens_out,
                          "cost_cny": float(sp.cost_cny) if sp.cost_cny is not None else None})
    return {"run_id": str(rid), "trace_id": run.trace_id, "items": items}
