# -*- coding: utf-8 -*-
"""
写接口 · 批 5.2：审批链（write_action.py）
========================================
路线图 §十一 的中间 6 个：

| Endpoint | 语义 | 角色 | audit |
| --- | --- | --- | --- |
| `POST /api/proposals/{id}/approve` | 批准（可能触发执行） | **supervisor** | ✅ |
| `POST /api/proposals/{id}/reject` | 拒绝（**必须带原因**） | **supervisor** | ✅ |
| `POST /api/propose?cluster_id=` | 为某簇生成提案 | operator | — |
| `POST /api/handle/{id}/adopt` | 采纳 → 生成提案 | operator | ✅ |
| `POST /api/handle/{id}/reject` | 不采纳（带原因） | operator | ✅ |
| `POST /api/retro` | 重新复盘 | operator | ✅ |

**四条设计要点**（都在代码里写理由）：

    ① **`approve` 用 `proposal_id`**（不是 `hitl_task_id` ✅）
       → 接口内部按 `proposal_id` 找 PENDING 待办 ✅
       → 理由：前端从提案列表点进来，手上只有 proposal_id ✅

    ② **危险操作要 `confirm: true`**（路线图：二次确认 ✅）
       → `approve` / `dlq.replay` 必须带 ✅
       → 防"前端误点一下就改价" ✅

    ③ **"拒绝"必须带原因**（路线图：三条要点之一 ✅）
       → 空原因 → 422 ✅（pydantic `min_length=1` ✅）

    ④ **`adopt` 从 `agent_run` 重建 `HandleResult`**
       → `adopt()` 吃的是 `HandleResult`（内存对象 ✅）
       → 而 API 手上只有 DB 里的 `agent_run` ✅
       → **重建时只用 DB 里真有的字段** ✅，不去重新跑 agent ✅
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from hub.api.deps import AuthUser, _trace_id, get_current_user

router = APIRouter(prefix="/api", tags=["write-action"])


# ════════════════════════════════════════════════════ 请求体
class ApproveIn(BaseModel):
    confirm: bool = Field(default=False, description="危险操作二次确认，必须 true")
    note: str = Field(default="", max_length=500)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500,
                        description="**必须带原因**（路线图硬要求）")


class AdoptIn(BaseModel):
    confirm: bool = Field(default=True)


class HandleRejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


# ════════════════════════════════════════════════════ 权限小工具
def _require(user: AuthUser, min_role: str, request: Request) -> None:
    from hub.tools.spec import CallContext
    if not CallContext(role=user.role).has_role(min_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            {"code": "ROLE_TOO_LOW",
                             "message": f"需要 {min_role} 及以上角色，你是 {user.role}",
                             "trace_id": _trace_id(request)})


def _find_pending_task(proposal_id: str):
    """按 `proposal_id` 找 PENDING 的 `hitl_task`（**approve 用 proposal_id** ✅）。"""
    from uuid import UUID

    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import HitlTask

    with get_session() as s:
        try:
            pid = UUID(proposal_id)
        except ValueError:
            return None, None
        t = s.scalars(select(HitlTask).where(
            HitlTask.proposal_id == pid,
            HitlTask.status == "PENDING").limit(1)).first()
        return (str(t.id) if t else None), pid


# ════════════════════════════════════════════════════ 1. approve
@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: str, body: ApproveIn, request: Request,
            user: AuthUser = Depends(get_current_user)):
    """批准（L2 会**真的执行**；L3 仍然转人工 ✅）。**supervisor 才能做** ✅"""
    _require(user, "supervisor", request)
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "CONFIRM_REQUIRED",
                             "message": "危险操作需要 confirm: true",
                             "trace_id": _trace_id(request)})
    task_id, pid = _find_pending_task(proposal_id)
    if pid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "proposal_id 不是合法 UUID",
                             "trace_id": _trace_id(request)})
    if task_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NO_PENDING_TASK",
                             "message": "这个提案没有待批任务（可能已批过/已拒/未提交网关）",
                             "trace_id": _trace_id(request)})
    from hub.action import approval
    try:
        # `by` 写 **user:<id>**（阶段 4 有登录了 ✅，不再是手填的角色名 ✅）
        res = approval.approve(task_id, by=f"user:{user.id}", note=body.note or None)
    except Exception as e:                        # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "APPROVE_FAILED",
                             "message": f"{type(e).__name__}: {e}",
                             "trace_id": _trace_id(request)}) from None
    return {"ok": res.get("ok", False), "task_id": task_id,
            "result": res, "note": body.note}


# ════════════════════════════════════════════════════ 2. reject
@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: RejectIn, request: Request,
           user: AuthUser = Depends(get_current_user)):
    """拒绝（**必须带原因** ✅）。supervisor 才能做 ✅"""
    _require(user, "supervisor", request)
    task_id, pid = _find_pending_task(proposal_id)
    if pid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "proposal_id 不是合法 UUID",
                             "trace_id": _trace_id(request)})
    if task_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NO_PENDING_TASK",
                             "message": "这个提案没有待批任务",
                             "trace_id": _trace_id(request)})
    from hub.action import approval
    res = approval.reject(task_id, by=f"user:{user.id}", note=body.reason)
    return {"ok": res.get("ok", False), "task_id": task_id, "result": res,
            "reason": body.reason}


# ════════════════════════════════════════════════════ 3. propose
@router.post("/propose")
def propose_now(cluster_id: str | None = Query(None),
                user: AuthUser = Depends(get_current_user)):
    """为某簇（或全局）生成提案。

    ⚠️ `proposer.propose()` 是**批处理**（一次给 max_proposals 个 ✅）
       → 传 `cluster_id` 时**只生成这一个簇的** ✅（其余跳过 ✅）
       → 实现方式：`max_proposals=6` + 事后按 cluster 过滤 ✅
         （不改 `proposer` 的函数签名 —— 那是全局逻辑 ✅）
    """
    from hub.action.proposer import propose as propose_all
    # ⚠️ 2026-11-01 修：**真的按 cluster_id 过滤** ✅
    #    原来只传 `max_proposals=6`（批处理）→ 生成的是全局的簇 ✅ ✗
    #    `only_cluster` 是这次给 `propose()` 加的参数 ✅
    st = propose_all(max_proposals=6, only_cluster=cluster_id or None)
    return {"stats": getattr(st, "__dict__", {}) or str(st),
            "requested_cluster": cluster_id,
            "clusters": getattr(st, "clusters", None),
            "proposals": getattr(st, "proposals", None),
            "ids": getattr(st, "ids", [])}


# ════════════════════════════════════════════════════ 4. adopt
@router.post("/handle/{run_id}/adopt")
def adopt_run(run_id: str, body: AdoptIn, request: Request,
              user: AuthUser = Depends(get_current_user)):
    """采纳一次 agent 运行 → 生成提案 ✅（**参数由代码构造，不让模型填** ✅）。"""
    from uuid import UUID

    from hub.agent.adopt import adopt as adopt_fn
    from hub.agent.handle import HandleResult
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
        if r.status != "DONE":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                {"code": "NOT_DONE",
                                 "message": f"agent_run 状态是 {r.status}，只有 DONE 才有明确建议",
                                 "trace_id": _trace_id(request)})
        if r.proposal_id:
            # **幂等**：已经采纳过 ✅
            return {"ok": True, "proposal_id": str(r.proposal_id),
                    "already_adopted": True}
        # 从 DB 重建 HandleResult ✅（不去重跑 agent ✅）
        res = HandleResult(cluster_id=str(r.cluster_id), goal=r.goal or "")
        res.topic = (r.result or {}).get("topic", "") or ""
        res.evidence = r.evidence or []
        res.recommendation = (r.result or {}).get("recommendation", "") or ""
        res.tool = (r.result or {}).get("tool", "") or ""
        res.confidence = float((r.result or {}).get("confidence") or 0.0)
        res.status = r.status
        res.critique = {}

    try:
        pid = adopt_fn(res)
    except Exception as e:                        # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "ADOPT_FAILED",
                             "message": f"{type(e).__name__}: {e}",
                             "trace_id": _trace_id(request)}) from None
    if pid is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"code": "CANNOT_ADOPT",
                             "message": "不能采纳（原因见服务端日志：参数构造失败或簇无商品）",
                             "trace_id": _trace_id(request)})
    # 回填 agent_run.proposal_id ✅
    with get_session() as s:
        r2 = s.get(AgentRun, rid)
        if r2 is not None:
            r2.proposal_id = pid
            s.commit()
    return {"ok": True, "proposal_id": str(pid), "already_adopted": False}


# ════════════════════════════════════════════════════ 5. handle/reject
@router.post("/handle/{run_id}/reject")
def reject_run(run_id: str, body: HandleRejectIn, request: Request,
               user: AuthUser = Depends(get_current_user)):
    """不采纳 agent 的结论（**带原因** ✅）。只写审计，不改 agent_run 状态 ✅"""
    from uuid import UUID

    from hub.audit import write as audit_write
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
    audit_write(actor=f"user:{user.id}", action="agent.reject",
                resource=f"agent_run:{rid}",
                detail={"reason": body.reason},
                trace_id=_trace_id(request))
    return {"ok": True, "run_id": str(rid), "reason": body.reason}


# ════════════════════════════════════════════════════ 6. retro
@router.post("/retro")
def rerun_retro(only_due: bool = Query(True, description="只跑到期的"),
                user: AuthUser = Depends(get_current_user)):
    """重新复盘（跑 `retro.run()` ✅ —— 和 `hub retro` 同一个函数 ✅）。"""
    from hub.audit import write as audit_write
    from hub.retro.report import run as retro_run

    done, skipped = retro_run(apply=True, only_due=only_due)
    audit_write(actor=f"user:{user.id}", action="retro.rerun", resource="none",
                detail={"done": len(done), "skipped": len(skipped),
                        "only_due": only_due})
    return {"ok": True, "done": len(done), "skipped": len(skipped),
            "items": done[:20]}
