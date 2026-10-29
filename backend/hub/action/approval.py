# -*- coding: utf-8 -*-
"""
审批流（approval.py）
====================
L2/L3 的提案会建 `hitl_task`（PENDING），等人点头。

    待办列表 → approve（批准 + 执行 + 回查） / reject（拒绝，什么都不做）

**为什么批准后才执行**：
    网关在 `_pending` 里**只建待办、不执行**（设计如此）。
    真正执行发生在 `approve` 里，传 `approved=True` 跳过"转审批"那一档。

**角色规则**（写在 ToolSpec.required_role 里，不写死在这里）：
    L2 改描述 → operator 就能批
    L3 改价   → 必须 manager
    角色不够时**不执行**，待办保持 PENDING（不会因为批错人就把事做了）
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from hub.gateway import Decision, ToolCall, invoke
from hub.persistence.db import get_session
from hub.persistence.models import ActionProposal, HitlTask
from hub.tools.spec import CallContext, get as get_spec


def list_pending() -> list[dict]:
    """所有待办（含提案信息），按创建时间正序。"""
    with get_session() as s:
        rows = s.execute(
            select(HitlTask, ActionProposal)
            .join(ActionProposal, ActionProposal.id == HitlTask.proposal_id)
            .where(HitlTask.status == "PENDING")
            .order_by(HitlTask.created_at)
        ).all()
    return [
        {
            "task_id": str(t.id),
            "proposal_id": str(p.id),
            "tool": p.tool_name,
            "risk": p.risk_level,
            "rationale": p.rationale,
            "args_preview": _preview(p.args),
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        }
        for t, p in rows
    ]


def _preview(args: dict) -> str:
    """参数预览：长文本截断，列表只显示条数。"""
    parts = []
    for k, v in (args or {}).items():
        if isinstance(v, list):
            parts.append(f"{k}[{len(v)}]")
        elif isinstance(v, str) and len(v) > 40:
            parts.append(f"{k}={v[:40]}…")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _load(task_id: str) -> tuple[HitlTask, ActionProposal]:
    with get_session() as s:
        task = s.get(HitlTask, UUID(task_id))
        if task is None:
            raise LookupError(f"待办不存在：{task_id}")
        proposal = s.get(ActionProposal, task.proposal_id)
        if proposal is None:
            raise LookupError(f"待办 {task_id} 指向的提案不存在")
        # 读出来就 detach（下面要跨 session 用）
        s.expunge(task)
        s.expunge(proposal)
    return task, proposal


def _decide(task_id: str, status: str, by: str, note: str | None) -> None:
    with get_session() as s:
        task = s.get(HitlTask, UUID(task_id))
        if task is None:
            raise LookupError(f"待办不存在：{task_id}")
        task.status = status
        task.assignee = by
        task.decision_note = note
        task.decided_at = datetime.now(timezone.utc)
        s.commit()


def reject(task_id: str, by: str, note: str | None = None) -> dict:
    """拒绝：待办标记 REJECTED，**不执行任何东西**。"""
    task, _ = _load(task_id)
    if task.status != "PENDING":
        return {"ok": False, "reason": f"待办状态是 {task.status}，不能拒绝"}
    _decide(task_id, "REJECTED", by, note)
    return {"ok": True, "status": "REJECTED"}


def approve(task_id: str, by: str, note: str | None = None) -> dict:
    """批准：先查角色，通过了才执行 + 回查。

    :param by: 批准人角色（operator / manager）。必须是角色名，不是人名——
               demo 里没有用户体系，用角色代表"谁有这个权限"。
    """
    task, proposal = _load(task_id)
    if task.status != "PENDING":
        return {"ok": False, "reason": f"待办状态是 {task.status}，不能重复批准"}

    spec = get_spec(proposal.tool_name)
    if spec is None:
        return {"ok": False, "reason": f"工具未注册：{proposal.tool_name}"}

    # 角色检查：走的是 ToolSpec.required_role（工具自己声明的），不是这里硬编码
    ctx = CallContext(role=by, trace_id=task.trace_id or "",
                      proposal_id=str(proposal.id))
    if not ctx.has_role(spec.required_role):
        return {
            "ok": False,
            "reason": f"角色不够：这个动作需要 {spec.required_role}，你是 {by}",
            "required_role": spec.required_role,
        }

    # 先把待办标成 APPROVED，再执行（执行失败也不会重复批）
    _decide(task_id, "APPROVED", by, note)

    refs = {}
    for item in (proposal.evidence_refs or []):
        if isinstance(item, dict):
            refs.update(item)

    call = ToolCall(
        tool_name=proposal.tool_name,
        args=proposal.args,
        evidence_refs=refs,
        proposal_id=str(proposal.id),
    )
    result = invoke(call, ctx, approved=True)     # ← 关键：approved=True 才会真执行

    # 把执行状态回写到提案
    with get_session() as s:
        p = s.get(ActionProposal, proposal.id)
        if p is not None:
            p.status = ("EXECUTED" if result.decision == Decision.EXECUTED
                        else result.decision.value)
            p.dry_run = False
            s.commit()

    return {
        "ok": result.ok,
        "decision": result.decision.value,
        "deny_code": result.deny_code,
        "verify_ok": result.verify_ok,
        "detail": result.detail,
        "execution_id": result.execution_id,
    }
