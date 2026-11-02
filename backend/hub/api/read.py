# -*- coding: utf-8 -*-
"""
读接口（read.py）—— 阶段 4.4a：8 个基础读
=========================================
**全部只读**，全部要 JWT，全部复用已有的 collect()（**不复制业务逻辑** ✅）：

| 接口 | 复用 | 说明 |
| --- | --- | --- |
| `GET /api/today` | `interact.today.collect()` | 运营第一眼看的 |
| `GET /api/status` | `observability.status.collect()` | 库里现状 |
| `GET /api/health` | `observability.health.collect()` | 最近一次运行的健康度 |
| `GET /api/brief` | — | **还没有数据源**（阶段 3.6 未做）→ 明确说"没生成过" ✅ |
| `GET /api/eval/latest` | `eval.store.history()` | 最近一次门禁 |
| `GET /api/proposals` | `action_proposal` | 分页 |
| `GET /api/proposals/{id}` | `action_proposal` + experience | 详情 |
| `GET /api/audit` | `audit_log` | 分页 + 筛选 |

⚠️ **为什么复用 collect() 而不是重写 SQL**：
    `hub today` 和 `GET /api/today` 必须**回答同一件事** ✅
    如果两边各写一套 SQL，迟早会出现"命令行说 3 件、网页说 5 件" ✅ ✗
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from hub.api.deps import AuthUser, _trace_id, get_current_user

router = APIRouter(prefix="/api", tags=["read"])


# ════════════════════════════════════════════════════ 1. today
@router.get("/today")
def get_today(user: AuthUser = Depends(get_current_user)):
    """今天该做什么（和 `hub today` **同一个数据源** ✅）

    ⚠️ `collect()` 返回的是 **`Today` 数据类**（`must_do` / `should_do` /
       `waiting` / `anomalies`），不是 dict ✅
       → 用 `dataclasses.asdict` 转，**不要手写字段映射**（加字段时会漏 ✅）
    """
    import dataclasses

    from hub.interact.today import collect
    return dataclasses.asdict(collect())


# ════════════════════════════════════════════════════ 2. status
@router.get("/status")
def get_status(user: AuthUser = Depends(get_current_user)):
    """业务数据现状（和 `hub status` 同一个数据源 ✅）"""
    from hub.observability.status import collect
    return {"sections": collect()}


# ════════════════════════════════════════════════════ 3. health
@router.get("/health")
def get_health(trace_id: str | None = Query(None, description="指定 trace_id；空=最近一次"),
               user: AuthUser = Depends(get_current_user)):
    """最近一次运行的健康度（工程视角 ✅）"""
    from hub.observability.health import collect
    return collect(trace_id)


# ════════════════════════════════════════════════════ 4. brief
@router.get("/brief")
def get_brief(user: AuthUser = Depends(get_current_user)):
    """AI 日报（阶段 3.6 ✅ 已实现）。

    读 `hub.brief.latest()` —— **和 `hub brief` 同一个数据源** ✅
    没有生成过时返回 `available: false` + 原因 ✅（不返回假数据 ✅）
    """
    from hub.brief import latest
    rows = latest(1)
    if not rows:
        return {"available": False,
                "reason": "还没生成过日报 —— 跑 `hub brief` 或等定时任务",
                "items": []}
    r = rows[0]
    # ⚠️ **`inputs`（来源指标）要现算**（2026-11-01 修）
    #   `brief` 生成时用 `collect_inputs()` 拉了一批数字，**只有它知道每个数字的来源** ✅
    #   而 `latest()` 只返回文本 ✅ → 前端"来源指标"那块就空了 ✅ ✗
    #   → 这里**重新拉一次**（只读聚合，几十毫秒 ✅），把扁平的数字给前端 ✅
    inputs: dict = {}
    try:
        from hub.brief import collect_inputs

        # ⚠️ **不要把 `_flatten_numbers` 用在这里**（2026-11-01 踩过 ✅）
        #    那个函数是**校验累加器**：签名 `(obj, acc: set[str], depth)`、
        #    把数字**格式化成字符串**收进 set、**返回 None** ✅
        #    → 它的用途是"这堆数字够不够校验日报"，**不是给人看的** ✅
        #    → 我要的是 **"指标名 → 值"**，所以自己拍平嵌套 dict ✅
        def _flat(obj, prefix: str = "", out: dict | None = None,
                  depth: int = 0) -> dict:
            out = {} if out is None else out
            if depth > 4:
                return out
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _flat(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
            elif isinstance(obj, (list, tuple)):
                # ⚠️ **`[label, value]` 二元组要特殊处理**（2026-11-01 修）
                #    `collect_inputs()` 里的 `items` 就是这种形状 ✅
                #    → 拍平后应该直接是 **`{label: value}`**，而不是 `status[0][1][0]` ✅
                if (len(obj) == 2 and isinstance(obj[0], str)
                        and isinstance(obj[1], (int, float))
                        and not isinstance(obj[1], bool)):
                    out[obj[0]] = obj[1]
                    return out
                if prefix:
                    out[f"{prefix}.n"] = len(obj)
                for i, v in enumerate(obj[:3]):
                    _flat(v, f"{prefix}[{i}]", out, depth + 1)
            elif isinstance(obj, bool) or obj is None:
                return out
            elif isinstance(obj, (int, float)):
                # ⚠️ **只收数字**（2026-11-01 修）
                #    第一版连字符串也收 → 前端"来源指标"会显示一堆
                #    `status[0][0] = 规模与影响` 这种噪音 ✅ ✗
                #    "来源指标"要的是**能被统计的数字** ✅
                out[prefix] = obj
            return out

        inputs = _flat(collect_inputs())
    except Exception as e:                        # noqa: BLE001
        inputs = {"__error__": f"{type(e).__name__}: {e}"}
    return {"available": True, "generated_at": r["created_at"],
            "text": r["text"], "lines": (r["text"] or "").splitlines(),
            "inputs": inputs,
            "counts": r["counts"], "fallback_table": r["fallback_table"]}


# ════════════════════════════════════════════════════ 5. eval/latest
@router.get("/eval/latest")
def get_eval_latest(user: AuthUser = Depends(get_current_user)):
    """最近一次门禁结果（`hub eval` 的落库结果，阶段 2 ✅）"""
    from hub.eval.store import history
    rows = history(1)
    if not rows:
        return {"available": False, "reason": "还没跑过 hub eval", "data": None}
    return {"available": True, "data": rows[0]}


# ════════════════════════════════════════════════════ 6. proposals
@router.get("/proposals")
def list_proposals(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status",
                                      description="PROPOSED/APPROVED/REJECTED/EXECUTED/SKIPPED"),
    topic: str | None = Query(None),
    user: AuthUser = Depends(get_current_user),
):
    """提案列表（分页 + 筛选）。**只返回摘要**，详情走 /proposals/{id} ✅"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import ActionProposal

    stmt = select(ActionProposal)
    cnt = select(func.count()).select_from(ActionProposal)
    if status_filter:
        stmt = stmt.where(ActionProposal.status == status_filter)
        cnt = cnt.where(ActionProposal.status == status_filter)
    if topic:
        stmt = stmt.where(ActionProposal.topic == topic)
        cnt = cnt.where(ActionProposal.topic == topic)
    with get_session() as s:
        total = int(s.scalar(cnt) or 0)
        rows = s.scalars(stmt.order_by(ActionProposal.created_at.desc())
                         .offset(offset).limit(limit)).all()
        items = [{"id": str(r.id), "tool_name": r.tool_name,
                  "risk_level": r.risk_level, "status": r.status,
                  "topic": r.topic, "rationale": (r.rationale or "")[:200],
                  "created_at": r.created_at.isoformat() if r.created_at else None}
                 for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# ════════════════════════════════════════════════════ 7. proposals/{id}
@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, request: Request,
                 user: AuthUser = Depends(get_current_user)):
    """提案详情：参数 + 依据 + 理由 + **同类问题的历史战绩**（图⑤）✅

    图⑤「同类问题历史战绩条形」**不单独开接口** —— 就是这里的 `experience` 区块 ✅
    （路线图 §10.4 明确要求 ✅）
    """
    from uuid import UUID

    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import ActionProposal

    try:
        pid = UUID(proposal_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "提案 id 不是合法 UUID",
                             "trace_id": _trace_id(request)}) from None
    with get_session() as s:
        p = s.get(ActionProposal, pid)
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "提案不存在",
                                 "trace_id": _trace_id(request)})
        out = {"id": str(p.id), "run_id": str(p.run_id), "trace_id": p.trace_id,
               "tool_name": p.tool_name, "tool_version": p.tool_version,
               "args": p.args, "risk_level": p.risk_level,
               "evidence_refs": p.evidence_refs, "rationale": p.rationale,
               "dry_run": p.dry_run, "status": p.status, "topic": p.topic,
               "created_at": p.created_at.isoformat() if p.created_at else None}

    # 同类问题历史战绩（图⑤）—— 按 topic 统计 ✅
    # ⚠️ `history_for(topic)` **只收一个参数**（2026-10-31 核对）✅
    #    第一版传了 (topic, tool_name) → TypeError → experience_error 里有原因 ✅
    try:
        from hub.action.experience import history_for
        recs = history_for(out.get("topic") or "")
        out["experience"] = [{"tool": r.tool, "effective": r.effective,
                              "harmful": r.harmful, "unclear": r.unclear,
                              "waiting": r.waiting,
                              "conclusion": r.conclusion}
                             for r in recs]
    except Exception as e:                        # noqa: BLE001
        # 战绩算不出来**不该让详情 500** ✅ → 给 null 并说明 ✅
        out["experience"] = None
        out["experience_error"] = f"{type(e).__name__}: {e}"
    return out


# ════════════════════════════════════════════════════ 8. audit
@router.get("/audit")
def list_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, description="按 action 筛，如 proposal.approve"),
    actor: str | None = Query(None),
    user: AuthUser = Depends(get_current_user),
):
    """操作日志（分页 + 筛选）。图⑰ 的表格数据源 ✅"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import AuditLog

    stmt = select(AuditLog)
    cnt = select(func.count()).select_from(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
        cnt = cnt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
        cnt = cnt.where(AuditLog.actor == actor)
    with get_session() as s:
        total = int(s.scalar(cnt) or 0)
        rows = s.scalars(stmt.order_by(AuditLog.id.desc())
                         .offset(offset).limit(limit)).all()
        items = [{"id": r.id, "at": r.created_at.isoformat() if r.created_at else None,
                  "actor": r.actor, "action": r.action, "resource": r.resource,
                  "detail": r.detail, "ip": str(r.client_ip) if r.client_ip else None}
                 for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# ════════════════════════════════════════════════════ 9. notify 列表（前端"通知中心"）
# 2026-11-01 补：之前只有 `POST /notify/{id}/read`（标记已读 ✅）
#   → **前端打开"通知中心"页，没有任何接口能列出通知** ✅ ✗
#   → 比喻：有"标记已读"的按钮，但没有"信箱" ✅
@router.get("/notify")
def list_notify(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    event_type: str | None = Query(None),
    user: AuthUser = Depends(get_current_user),
):
    """通知列表（分页+筛选）+ **未读数**（前端铃铛上的数字 ✅）。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import Notification

    stmt = select(Notification)
    cnt = select(func.count()).select_from(Notification)
    if status_filter:
        stmt = stmt.where(Notification.status == status_filter)
        cnt = cnt.where(Notification.status == status_filter)
    if event_type:
        stmt = stmt.where(Notification.event_type == event_type)
        cnt = cnt.where(Notification.event_type == event_type)
    with get_session() as s:
        total = int(s.scalar(cnt) or 0)
        # **未读数**：前端"🔔 3"就是它 ✅（READ 之外都算未读 ✅）
        unread = int(s.scalar(
            select(func.count()).select_from(Notification)
            .where(Notification.status != "READ")) or 0)
        rows = s.scalars(stmt.order_by(Notification.id.desc())
                         .offset(offset).limit(limit)).all()
        items = [{"id": n.id, "event_type": n.event_type,
                  "resource_id": n.resource_id, "channel": n.channel,
                  "status": n.status, "attempts": n.attempts,
                  "payload": n.payload,
                  "created_at": n.created_at.isoformat() if n.created_at else None,
                  "sent_at": n.sent_at.isoformat() if n.sent_at else None}
                 for n in rows]
    return {"total": total, "unread": unread, "limit": limit, "offset": offset,
            "items": items}


# ════════════════════════════════════════════════════ 10. knowledge 列表（"知识库"页）
# 2026-11-01 补：之前只有 `POST /knowledge`（建）和 `/publish`（发布 ✅）
#   → **前端打开"知识库"页，看不到自己发了什么** ✅ ✗
#   → 这个缺口比通知那个严重（没有替代接口 ✅）
@router.get("/knowledge")
def list_knowledge(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    type_filter: str | None = Query(None, alias="type"),
    user: AuthUser = Depends(get_current_user),
):
    """知识条目列表 + 每条**现在是否生效** ✅

    为什么返回 `effective`：
        运营最关心"我发的这条**现在有没有在影响 AI**" ✅ = `status == PUBLISHED` ✅
        → 直接给布尔 + 一句人话，前端不用自己推 ✅
    """
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import KnowledgeEntry

    stmt = select(KnowledgeEntry)
    cnt = select(func.count()).select_from(KnowledgeEntry)
    if status_filter:
        stmt = stmt.where(KnowledgeEntry.status == status_filter)
        cnt = cnt.where(KnowledgeEntry.status == status_filter)
    if type_filter:
        stmt = stmt.where(KnowledgeEntry.type == type_filter)
        cnt = cnt.where(KnowledgeEntry.type == type_filter)
    with get_session() as s:
        total = int(s.scalar(cnt) or 0)
        rows = s.scalars(stmt.order_by(KnowledgeEntry.created_at.desc())
                         .offset(offset).limit(limit)).all()
        items = []
        for k in rows:
            ref = k.source_ref or {}
            topic = ref.get("topic") if isinstance(ref, dict) else None
            items.append({
                "id": str(k.id), "type": k.type, "title": k.title,
                "content": k.content, "topic": topic,
                "status": k.status, "version": int(k.version or 1),
                "effective": k.status == "PUBLISHED",
                "effective_note": ("已注入提示词 ✅" if k.status == "PUBLISHED"
                                   else "DRAFT 不生效，发布后才注入 ✅"),
                "created_at": k.created_at.isoformat() if k.created_at else None,
            })
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# ════════════════════════════════════════════════════ 11. 簇的评价原文（"依据展开"）
# 2026-11-01 补（用户要求）：
#   产品最大的卖点之一是「点开提案 → 看到这条建议依据的是**哪几条真实评价**」✅
#   而 `action_proposal.evidence_refs` 只有 `{"issue_cluster": "<uuid>"}` ✅
#     → 只到簇这一层，**没有原文** ✅ ✗ → 前端"展开依据"做不了 ✅
#   ⚠️ `record.content_clean` 在**入库时已做 PII 脱敏** ✅（`ingress/pii.py`）
#      → 可以直接给前端 ✅（`record` 表**没有** `content_raw` 列，核对过 ✅）
@router.get("/clusters/{cluster_id}/reviews")
def list_cluster_reviews(
    cluster_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    only_negative: bool = Query(False, description="只看差评（rating ≤ 2）"),
    user: AuthUser = Depends(get_current_user),
):
    """某个问题簇的**成员评价原文**（按代表性 + 时间排序）。

    为什么按"代表性"排：
        `cluster_member.is_representative` 标的是"这条是簇的代表样本" ✅
        → 展开依据时，先看代表样本比"最新一条"有用得多 ✅
    """
    from uuid import UUID

    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import (ClusterMember, IssueCluster, Record,
                                        ReviewTopic)

    try:
        cid = UUID(cluster_id)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail={"code": "BAD_ID",
                                    "message": "cluster_id 不是合法 UUID",
                                    "trace_id": _trace_id(request)}) from None

    with get_session() as s:
        cl = s.get(IssueCluster, cid)
        if cl is None:
            # 不存在 → 404（**不要返回空数组**，否则前端以为"这个簇没评价" ✅）
            raise HTTPException(status_code=404,
                                detail={"code": "NOT_FOUND",
                                        "message": f"簇不存在：{cluster_id}",
                                        "trace_id": _trace_id(request)})

        stmt = (select(ClusterMember, Record)
                .join(Record, Record.id == ClusterMember.record_id)
                .where(ClusterMember.cluster_id == cid))
        cnt = (select(func.count()).select_from(ClusterMember)
               .where(ClusterMember.cluster_id == cid))
        if only_negative:
            stmt = stmt.where(Record.rating <= 2)
            cnt = (select(func.count()).select_from(ClusterMember)
                   .join(Record, Record.id == ClusterMember.record_id)
                   .where(ClusterMember.cluster_id == cid, Record.rating <= 2))
        total = int(s.scalar(cnt) or 0)

        rows = s.execute(
            stmt.order_by(ClusterMember.is_representative.desc(),
                          Record.review_time.desc())
            .offset(offset).limit(limit)).all()

        # topic / severity 现查（避免 N+1 ✅）
        rids = [r.id for _m, r in rows]
        tp: dict = {}
        if rids:
            for rid, t, sev in s.execute(
                    select(ReviewTopic.record_id, ReviewTopic.topic,
                           ReviewTopic.severity).where(
                        ReviewTopic.record_id.in_(rids))).all():
                tp[rid] = (t, sev)

        items = []
        for m, r in rows:
            t, sev = tp.get(r.id, (None, None))
            items.append({
                "record_id": str(r.id),
                "platform": r.platform,
                "platform_spu_id": r.platform_spu_id,
                "rating": r.rating,
                "review_time": r.review_time.isoformat() if r.review_time else None,
                "content": r.content_clean or "",
                "topic": t,
                "severity": float(sev) if sev is not None else None,
                "is_representative": bool(m.is_representative),
            })
        label = cl.label
    return {"cluster_id": str(cid), "label": label, "total": total,
            "limit": limit, "offset": offset, "items": items}
