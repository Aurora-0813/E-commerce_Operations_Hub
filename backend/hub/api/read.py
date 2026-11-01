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
    return {"available": True, "generated_at": r["created_at"],
            "text": r["text"], "lines": (r["text"] or "").splitlines(),
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
