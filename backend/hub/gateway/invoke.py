# -*- coding: utf-8 -*-
"""
网关主逻辑（invoke.py）
======================
四道防线，**顺序不可交换**：

    ① 工具存在性 + 参数强校验 + 角色
    ② 依据（类别齐全 / 真实存在 / 对得上东西）
    ③ 幂等（查询）      ← 放在频控【前】：重复请求要快速返回
    ④ 频控（令牌桶）
    ③ 幂等（占位）      ← 放在频控【后】：被限流的不该白占幂等键
    ⑤ 分级：L2 转审批 / L3 转人工 / L0~L1 执行

**为什么分级放在最后**：
    校验没过就不该占用审批资源。"这个动作根本不合规"和"这个动作要人批"是两回事。

**每次调用（包括被拒）都写 tool_execution**：
    审计要能回答"谁在什么时候想做什么、为什么没做成"。
"""
import time
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from hub.gateway import faults
from hub.gateway.checks import (
    LIMITER,
    EvidenceError,
    check_evidence,
    idem_claim,
    idem_key,
    idem_lookup,
    idem_release,
)
from hub.gateway.schemas import Decision, DenyCode, GatewayResult, ToolCall
from hub.persistence.db import get_session
from hub.persistence.models import ToolExecution
from hub.tools.spec import CallContext, RiskLevel, get


def invoke(call: ToolCall, ctx: CallContext, *, approved: bool = False) -> GatewayResult:
    """走一遍网关。返回判定结果（不抛异常，除非是程序 bug）。

    :param approved: True 表示"人已经批了"——此时 L2/L3 **直接执行**（跳过转审批）。
                     只有 `hub approve` 在人工批准之后才会传 True。

    另外：**每次判定后都会回写 `action_proposal.status`**。
    为什么：L0/L1 是网关自己执行掉的，不走 `hub approve`；
    如果这里不同步，提案状态会一直停在 PROPOSED，
    而"已执行几个"这类报表就会读出一个假的 0（实测踩过）。
    """
    result = _invoke_inner(call, ctx, approved=approved)
    _sync_proposal_status(call.proposal_id, result.decision)
    return result


def _sync_proposal_status(proposal_id: str, decision: Decision) -> None:
    """把判定结果写回提案（这样 `hub status` 才算得出真实的执行率）。"""
    if not proposal_id:
        return
    try:
        from hub.persistence.models import ActionProposal
        with get_session() as s:
            p = s.get(ActionProposal, UUID(proposal_id))
            if p is None:
                return
            p.status = decision.value
            if decision == Decision.EXECUTED:
                p.dry_run = False
            s.commit()
    except Exception:                            # noqa: BLE001  状态同步失败不影响主流程
        pass


def _invoke_inner(call: ToolCall, ctx: CallContext, *, approved: bool = False) -> GatewayResult:
    t0 = time.time()

    # ---------- ① 存在性 ----------
    spec = get(call.tool_name)
    if spec is None:
        return _reject(call, ctx, DenyCode.TOOL_NOT_FOUND,
                       {"tool": call.tool_name}, t0)

    # ---------- ① 参数强校验（extra="forbid"，多余字段直接报错）----------
    try:
        # mode="json"：把 Decimal / datetime 等转成 JSON 安全类型，
        # 否则写 tool_execution.request（JSONB）时会炸 "Decimal is not JSON serializable"
        args = spec.params_model.model_validate(call.args).model_dump(mode="json")
    except ValidationError as e:
        return _reject(call, ctx, DenyCode.PARAM_INVALID,
                       {"errors": e.errors(include_url=False)}, t0)

    # ---------- ① 角色 ----------
    if not ctx.has_role(spec.required_role):
        return _reject(call, ctx, DenyCode.SCOPE_DENIED,
                       {"required": spec.required_role, "role": ctx.role}, t0)

    # ---------- ② 依据 ----------
    try:
        check_evidence(spec, args, call.evidence_refs)
    except EvidenceError as e:
        return _reject(call, ctx, e.code, e.detail, t0)

    # ---------- ③ 幂等（查询）----------
    key = idem_key(spec, args, ctx)
    prev_execution = idem_lookup(key)
    if prev_execution:
        return _replay(call, ctx, spec, prev_execution, t0)

    # ---------- ④ 频控 ----------
    if not LIMITER.acquire(f"{ctx.tenant_id}:{spec.name}"):
        return _reject(call, ctx, DenyCode.RATE_LIMITED,
                       {"qps": LIMITER.qps, "burst": LIMITER.burst}, t0)

    # ---------- ⑤ 分级：L3 转人工，L2 转审批（都不执行）----------
    # approved=True 表示"人已经批过了"，直接往下执行
    if not approved:
        if spec.risk_level == RiskLevel.L3:
            return _pending(call, ctx, Decision.PENDING_HITL, spec, args, t0)
        if spec.risk_level == RiskLevel.L2:
            return _pending(call, ctx, Decision.PENDING_APPROVAL, spec, args, t0)

    # ---------- ③ 幂等（占位）→ 执行 → 回查 ----------
    return _execute(call, ctx, spec, args, key, t0)


# ==================== 执行 ====================

def _execute(call: ToolCall, ctx: CallContext, spec, args: dict,
             key: str, t0: float) -> GatewayResult:
    """L0/L1（或已批准的 L2/L3）：占位 → 执行（带重试）→ 回查 → 失败补偿 → 审计。"""
    execution_id = _start_execution(call, ctx, args)

    if not idem_claim(key, spec, execution_id):
        _delete_execution(execution_id)
        prev = idem_lookup(idem_key(spec, args, ctx))
        if prev:
            return _replay(call, ctx, spec, prev, t0)
        return _reject(call, ctx, DenyCode.RATE_LIMITED, {"reason": "占位失败"}, t0)

    # ---------- 执行（带重试）----------
    response, last_error, attempt = None, None, 0
    for attempt in range(1, faults.MAX_ATTEMPTS + 1):
        try:
            faults.maybe_fail(spec, attempt)      # 故障注入（演示用）
            response = spec.handler(args, ctx)
            last_error = None
            break
        except Exception as e:                    # noqa: BLE001
            last_error = e
            if attempt < faults.MAX_ATTEMPTS:
                print(f"    [重试] {spec.name} 第 {attempt} 次失败：{e}")
                faults.backoff_sleep(attempt)

    if last_error is not None:
        # 重试耗尽：不知道写没写进去 → 直接死信 + 等人工核对（不瞎补偿）
        idem_release(key)
        _dead_letter(spec, args, last_error, attempt, ctx)
        _finish_execution(execution_id, Decision.REJECTED, error=last_error, t0=t0)
        return GatewayResult(
            decision=Decision.REJECTED, tool_name=spec.name,
            execution_id=execution_id, deny_code=DenyCode.HANDLER_ERROR,
            detail={"error": f"{type(last_error).__name__}: {last_error}",
                    "attempts": attempt, "dead_letter": True},
        )

    # ---------- 回查（不信 handler 的返回值）----------
    if faults.should_fail_verify(spec):
        verify_ok, verify_detail = False, {"injected": "回查故障注入"}
    else:
        try:
            vr = spec.verify(args, ctx, response)
            verify_ok, verify_detail = vr.ok, vr.detail
        except Exception as e:                    # noqa: BLE001
            verify_ok, verify_detail = False, {"error": str(e)}

    # ---------- 回查失败 → 补偿 ----------
    compensated = None
    if not verify_ok:
        compensated = _compensate(spec, args, response, ctx)
        if compensated.get("ok"):
            # 补偿成功：状态已回到执行前，按"未生效"处理
            idem_release(key)                     # 允许重来
        else:
            _dead_letter(spec, args,
                         RuntimeError(f"回查失败且补偿失败：{compensated}"), 1, ctx)
            _finish_execution(execution_id, Decision.REJECTED,
                              response=response, verify_ok=False,
                              deny_code=DenyCode.VERIFY_FAILED, t0=t0)
            return GatewayResult(
                decision=Decision.REJECTED, tool_name=spec.name,
                execution_id=execution_id, response=response,
                deny_code=DenyCode.VERIFY_FAILED, verify_ok=False,
                detail={"verify": verify_detail, "compensate": compensated},
            )

    _finish_execution(execution_id, Decision.EXECUTED,
                      response=response, verify_ok=verify_ok, t0=t0)

    return GatewayResult(
        decision=Decision.EXECUTED,
        tool_name=spec.name,
        execution_id=execution_id,
        response=response,
        verify_ok=verify_ok,
        detail={"verify": verify_detail, "risk": spec.risk_level,
                "attempts": attempt, "compensate": compensated},
    )


def _compensate(spec, args: dict, response: dict, ctx: CallContext) -> dict:
    """回查失败时尝试回滚。

    补偿参数从 handler 的返回值里取（比如 update_desc 会返回 old_description）——
    因为"执行前的值"只有 handler 知道。
    没有 compensate 的工具（L3 改价）直接返回失败，走死信。
    """
    if not spec.compensate:
        return {"ok": False, "reason": "该工具不可逆，没有补偿动作"}
    comp_spec = get(spec.compensate)
    if comp_spec is None:
        return {"ok": False, "reason": f"补偿工具未注册：{spec.compensate}"}

    comp_args = dict(args)
    if "old_description" in (response or {}):
        comp_args["description"] = response.get("old_description")
    try:
        comp_spec.handler(comp_args, ctx)
        vr = comp_spec.verify(comp_args, ctx, {})
        return {"ok": vr.ok, "action": spec.compensate, "detail": vr.detail}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _dead_letter(spec, args: dict, error: Exception, attempts: int,
                 ctx: CallContext) -> None:
    """进死信：人工后续处理。"""
    from hub.persistence.models import DeadLetter

    with get_session() as s:
        s.add(DeadLetter(
            stream=f"tool:{spec.name}",
            payload={"args": args, "trace_id": ctx.trace_id,
                     "role": ctx.role},
            error=f"{type(error).__name__}: {error}"[:500],
            attempts=attempts,
        ))
        s.commit()


def _replay(call: ToolCall, ctx: CallContext, spec, execution_id: str,
            t0: float) -> GatewayResult:
    """幂等命中：不重复执行，返回上次的结果。"""
    with get_session() as s:
        row = s.get(ToolExecution, UUID(execution_id))
        response = row.response if row else None
        verify_ok = row.verify_ok if row else None
    return GatewayResult(
        decision=Decision.REPLAYED,
        tool_name=spec.name,
        execution_id=execution_id,
        response=response,
        verify_ok=verify_ok,
        detail={"replay_of": execution_id},
    )


def _pending(call: ToolCall, ctx: CallContext, decision: Decision, spec,
             args: dict, t0: float) -> GatewayResult:
    """L2/L3：**不执行**，只返回"需要人"的判定 + 建一条待办。

    注意：这里**不占幂等键**——审批通过后真正执行时才占。
    否则"提案被拒/超时"会把键永久占住（虽然 24h 会过期，但没必要）。
    """
    execution_id = _start_execution(call, ctx, args)
    _finish_execution(execution_id, decision, t0=t0)

    # 建待办（同一个提案重复提交时不重复建）
    task_id = _ensure_hitl_task(call, ctx, spec, decision)

    return GatewayResult(
        decision=decision,
        tool_name=spec.name,
        execution_id=execution_id,
        detail={
            "risk": spec.risk_level,
            "required_role": spec.required_role,
            "hitl_task_id": task_id,
            "why": ("不可逆/涉及资金，必须人工批准后才执行"
                    if decision == Decision.PENDING_HITL
                    else "影响面有限，需审批"),
        },
    )


def _ensure_hitl_task(call: ToolCall, ctx: CallContext, spec, decision: Decision) -> str | None:
    """给 L2/L3 建人工待办。同一提案已有 PENDING 的就复用。

    为什么按 proposal_id 去重：
        重复提交同一个提案（比如重跑管线）不该刷出一堆待办，
        否则运营的待办列表会被同一个东西淹没。
    """
    from datetime import datetime, timedelta, timezone
    from hub.persistence.models import HitlTask

    if not call.proposal_id:
        return None
    pid = UUID(call.proposal_id)
    with get_session() as s:
        existing = s.scalars(
            select(HitlTask)
            .where(HitlTask.proposal_id == pid)
            .where(HitlTask.status == "PENDING")
            .limit(1)
        ).first()
        if existing is not None:
            return str(existing.id)
        task = HitlTask(
            proposal_id=pid,
            trace_id=ctx.trace_id or "no-trace",
            status="PENDING",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        s.add(task)
        s.flush()
        tid = str(task.id)
        s.commit()
    return tid


# ==================== 拒绝 ====================

def _reject(call: ToolCall, ctx: CallContext, code: str, detail: dict,
            t0: float) -> GatewayResult:
    """拒绝也要落审计——被拒的调用同样是"有人想做某件事"的证据。"""
    execution_id = _start_execution(call, ctx, call.args)
    _finish_execution(execution_id, Decision.REJECTED, deny_code=code, t0=t0)
    return GatewayResult(
        decision=Decision.REJECTED,
        tool_name=call.tool_name,
        execution_id=execution_id,
        deny_code=code,
        detail=detail,
    )


# ==================== 审计（tool_execution） ====================

def _start_execution(call: ToolCall, ctx: CallContext, args: dict) -> str:
    """建一条 RUNNING 的执行记录，返回 id。"""
    with get_session() as s:
        row = ToolExecution(
            proposal_id=UUID(call.proposal_id),
            trace_id=ctx.trace_id or "no-trace",
            tool_name=call.tool_name,
            attempt=1,
            request={"args": args, "evidence_refs": call.evidence_refs},
            decision="RUNNING",
            status="RUNNING",
        )
        s.add(row)
        s.flush()
        eid = str(row.id)
        s.commit()
    return eid


def _finish_execution(execution_id: str, decision: Decision, *,
                      response: dict | None = None, deny_code: str | None = None,
                      verify_ok: bool | None = None, error: Exception | None = None,
                      t0: float = 0.0) -> None:
    with get_session() as s:
        row = s.get(ToolExecution, UUID(execution_id))
        if row is None:
            return
        row.decision = decision.value
        row.status = "DONE" if decision == Decision.EXECUTED else decision.value
        row.response = response
        row.deny_code = deny_code
        row.verify_ok = verify_ok
        if error is not None:
            row.response = {"error": f"{type(error).__name__}: {error}"}
        row.duration_ms = int((time.time() - t0) * 1000) if t0 else None
        from datetime import datetime, timezone
        row.ended_at = datetime.now(timezone.utc)
        s.commit()


def _delete_execution(execution_id: str) -> None:
    with get_session() as s:
        row = s.get(ToolExecution, UUID(execution_id))
        if row is not None:
            s.delete(row)
            s.commit()
