# -*- coding: utf-8 -*-
"""
采纳 agent 的结论 → 生成提案 → 提交网关（adopt.py）
=====================================================
决策 #23 的落地：`hub handle` 的建议不只是打印，还能变成**真的动作**。

════════════════════════════════════════════════════════════════════
⚠️ 关键设计：args 由 proposer（代码）构造，**不由模型填**
════════════════════════════════════════════════════════════════════
依据是实测（`docs/开发指南/14_Agent规划能力实测.md`）：
    · 7B 在无规则时 **22/22 全选同一个工具**
    · 它 **6/6 都在编参数**，连 36 位 UUID 都抄错一位（946→986）

所以采纳的语义是：
    ① `proposer._build_args()` 构造**正确的 args**（和批处理提案同一条路径 ✅）
    ② agent 的建议原话写进 `rationale` ✅
    ③ 若 agent 选的工具和规则不同 → **分歧写进 rationale** ✅
    ④ 回填 `agent_run.proposal_id` ✅

两段是**分开的**，因为中间该有人按一下：
    `adopt()`   建议 → 提案（status=PROPOSED, dry_run=True）
    `submit()`  提案 → **提交网关**（这才真的会执行/转审批）
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from hub.agent.handle import HandleResult


# ════════════════════════════════════════════════════════════ 采纳
def adopt(result: HandleResult, *, llm=None) -> UUID | None:
    """把一次 `hub handle` 的结论变成一个正式提案（**不执行**）。

    :return: 新提案的 UUID；不能采纳时返回 None（并打印原因）
    """
    from hub.action.proposer import _build_args, _dominant_topic, _schema_error
    from hub.persistence.db import get_session
    from hub.persistence.models import (ActionProposal, AnalysisRun,
                                        ClusterMember, IssueCluster)
    from hub.tools.spec import get as get_spec

    if result.status != "DONE" or not result.tool:
        # NEEDS_HUMAN / FAILED 的结论**不该**变成提案 ✅（没有明确建议）
        print(f"  ⚠️ 不能采纳：agent 状态是 {result.status}（只有 DONE 才有明确建议）")
        return None

    # llm 兜底（和 `handle()` 同一个模式）：`_build_args` 要调模型写正文/描述
    # （实测踩过：忘了兜底 → `'NoneType' object has no attribute 'chat'` ✅）
    if llm is None:
        from hub.llm import get_text_llm
        llm = get_text_llm()

    spec = get_spec(result.tool)
    if spec is None:
        print(f"  ⚠️ 不能采纳：工具 {result.tool} 没注册")
        return None

    cid = UUID(result.cluster_id)
    with get_session() as s:
        cluster = s.get(IssueCluster, cid)
        if cluster is None:
            print(f"  ⚠️ 不能采纳：簇不存在 {cid}")
            return None

        # 取簇成员 —— **要 `ClusterMember` 对象**（不是 Record 行）✅
        # proposer 的 `_member_texts` / `_primary_spu` 用的是 `m.record_id`，
        # 然后自己按 id 去查文本（2026-10-31 核对过，不是猜的）
        members = list(s.scalars(
            select(ClusterMember).where(ClusterMember.cluster_id == cid)
        ).all())
        if not members:
            print(f"  ⚠️ 不能采纳：簇里没有成员（{cid}）")
            return None

        topic = _dominant_topic(cid, members) or (result.topic or "")

        # ---- ② 构造正确的 args（代码干，不是模型干 ✅）----
        try:
            args = _build_args(result.tool, cluster, members, topic, llm)
        except Exception as e:                    # noqa: BLE001
            print(f"  ⚠️ 不能采纳：构造参数失败（{type(e).__name__}: {e}）")
            return None

        # ---- 写库前按工具 schema 预校验（和 proposer 同一道关 ✅）----
        err = _schema_error(spec, args)
        if err:
            print(f"  ⚠️ 不能采纳：参数不符合工具 schema —— {err}")
            return None

        # ---- rationale：agent 的建议原话 + 分歧留痕 ----
        from hub.action.proposer import TOOL_BY_TOPIC
        rule_tool = TOOL_BY_TOPIC.get(topic, "")
        parts = [f"AI 建议：{result.recommendation}（置信度 {result.confidence:.0%}）"]
        if rule_tool and rule_tool != result.tool:
            parts.append(f"**与业务规则不一致**：规则是 {rule_tool}，agent 选了 {result.tool}")
        parts.append(f"依据 {len(result.evidence)} 条（E1..En，可追溯）")
        # Critic 的结论也要进提案理由（**让审批的人看到第二个模型怎么看** ✅）
        if result.critique:
            c = result.critique
            verdict = c.get("verdict") or "?"
            note = f"Critic：{verdict}"
            if c.get("problems"):
                note += f"（{'；'.join(c['problems'][:3])}）"
            if c.get("reason"):
                note += f"　理由：{c['reason']}"
            parts.append(note + ("　⚠️ **审核未通过，请重点看**" if verdict == "WRONG" else ""))

        # ---- 建 run + 提案（run_id 非空，起一条 adopt- 的 run ✅）----
        # ⚠️ `analysis_run.trace_id` 是 **UNIQUE** ✅
        #    第一次我写成 `f"adopt-{cid.hex[:10]}"` → 同一个簇**第二次采纳**就撞唯一约束 ✅ ✗
        #    **这和 `hub eval` 的 run_id 只精确到分钟是同一个病：**
        #       唯一字段，用了不唯一的值 ✅
        #    → 加随机后缀，保证"同一簇被采纳多次"也各有各的 run ✅
        from uuid import uuid4
        run = AnalysisRun(trace_id=f"adopt-{cid.hex[:10]}-{uuid4().hex[:6]}",
                          batch_id=None, tenant_id="default", status="DONE")
        s.add(run)
        s.flush()

        prop = ActionProposal(
            run_id=run.id,
            trace_id=run.trace_id,
            tool_name=result.tool,
            tool_version=spec.version,
            args=args,
            risk_level=spec.risk_level,
            evidence_refs=[{"issue_cluster": str(cid)}],
            rationale="；".join(parts),
            dry_run=True,
            status="PROPOSED",
            topic=topic or None,
        )
        s.add(prop)
        s.flush()
        pid = prop.id
        s.commit()

    print(f"  ✅ 已生成提案 {pid}（工具 {result.tool}，参数由代码构造）")
    if rule_tool and rule_tool != result.tool:
        print(f"     ⚠️ 注意：agent 与规则不一致（规则 {rule_tool}），已写进提案理由")
    return pid


# ════════════════════════════════════════════════════════════ 提交
def submit(proposal_id: UUID, *, role: str = "operator") -> dict:
    """把一条 `PROPOSED` 提案**提交网关**（走四道防线 + 风险分级）。

    ⚠️ 这是"提案 → 真的发生"的那一跳，所以要显式调用（`--submit`），
       **不跟 `--adopt` 绑在一起** ✅ ——
       "agent 的建议"和"要执行"之间应该有人按一下 ✅
       （哪怕 L1 是自动执行的，也该是"人决定提交"才发生 ✅）

    结果的含义（`Decision` 只有五种）：
        EXECUTED / REPLAYED  → 真的执行了（L0/L1）
        PENDING_APPROVAL     → L2，转待审批（`hub today` 里能看到）
        PENDING_HITL         → L3，转人工工单
        REJECTED             → 被拒（参数/权限/依据/频控）
    """
    from hub.gateway import invoke
    from hub.gateway.schemas import Decision, ToolCall
    from hub.persistence.db import get_session
    from hub.persistence.models import ActionProposal
    from hub.tools.spec import CallContext

    with get_session() as s:
        prop = s.get(ActionProposal, proposal_id)
        if prop is None:
            raise ValueError(f"提案不存在：{proposal_id}")
        tool_name = prop.tool_name
        # 依据要原样带上：网关会校验"依据存在 + 指向和动作一致" ✅
        refs = {}
        if prop.evidence_refs:
            refs = dict(prop.evidence_refs[0]) if isinstance(prop.evidence_refs[0], dict) else {}
        call = ToolCall(tool_name=tool_name, args=prop.args or {},
                        evidence_refs=refs, proposal_id=str(prop.id))

    ctx = CallContext(role=role, trace_id=f"submit-{str(proposal_id)[:8]}")
    res = invoke(call, ctx)

    print(f"  提交网关：{tool_name} → {res.decision.value}"
          + (f"（{res.deny_code}）" if res.deny_code else ""))
    if res.decision == Decision.PENDING_APPROVAL:
        print("     L2：已转待审批 —— `hub today` 的「要我做」里能看到 ✅")
    elif res.decision == Decision.PENDING_HITL:
        print("     L3：已转人工工单（不会自动执行 ✅）")
    elif res.decision == Decision.REJECTED:
        print(f"     被拒：{res.deny_code}　{res.detail}")

    return {"decision": res.decision.value,
            "ok": res.decision in (Decision.EXECUTED, Decision.REPLAYED),
            "deny_code": res.deny_code, "detail": res.detail,
            "execution_id": res.execution_id}
