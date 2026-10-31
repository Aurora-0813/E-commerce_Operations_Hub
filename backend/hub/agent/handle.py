# -*- coding: utf-8 -*-
"""
agent 主路径（handle.py）—— 形态 ③：受限 agent
===============================================
**一句话**：步骤和参数由**代码**给，模型只做**一次判断**（这些证据支持什么建议）。

为什么是这个形态 —— 不是妥协，是**实测结论**
（`docs/开发指南/14_Agent规划能力实测.md`）：
    · 7B **会**按合理顺序调工具（6/6 首步只读）✅
    · 7B **不会**收尾（6/6 用完 5 步都不给结论）✅ ✗
    · 7B **6/6 都在编参数**（`spu_id='待定'`、把 36 位 UUID 抄错一位）✅ ✗
   → 所以：**"选什么工具"和"参数填什么"交给代码；"证据说明什么"交给模型** ✅

四步（前 3 步和最后 1 步是代码，模型只在第 4 步出现）：

    ① query_cluster(cluster_id)        ← 代码给参数
    ② query_reviews(cluster_id, n=5)   ← 代码给参数
    ③ query_history(topic)              ← topic 来自 ① 的返回，不是模型编的
    ④ 【模型】给定证据，判断"支持什么建议"（只输出建议 + 引用了哪几条证据）
    ⑤ 组装 plan（代码）→ 落 agent_run

为什么第 ④ 步要求模型"引用证据编号"：
    这样既能校验（引用的编号必须存在 ✅），又能让前端展示"依据" ✅
    —— 和 M3 的"证据子串校验"同一个思路，只是用在本 agent 上 ✅
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from hub.tools.readonly import (MAX_REVIEWS, _query_cluster, _query_history,
                                _query_reviews)
from hub.tools.spec import CallContext

# 模型只允许给这几类建议（封闭集合 → 可校验、可统计）
ALLOWED_TOOLS = ("review.draft_reply", "listing.update_desc", "hitl.create")


@dataclass
class Step:
    """一步的轨迹。"""
    idx: int
    tool: str
    args: dict
    ok: bool
    chars: int = 0
    ms: int = 0
    error: str = ""


@dataclass
class HandleResult:
    """一次 agent 任务的产出。"""
    cluster_id: str
    goal: str
    # 问题类型（① 查簇时填）——**放在 result 对象上，不靠图的状态合并传递** ✅
    # 为什么：LangGraph 的 state 合并是"隐式契约"（节点 return 什么就并什么），
    # 而 result 是**同一个对象引用**，两版都一定跟着走 ✅
    # （实测踩过：图版 topic 没并进去 → 规则块没拼 → prompt 短 332 字 → 选错工具）
    topic: str = ""
    steps: list[Step] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)   # 每条带 source/ref
    recommendation: str = ""
    tool: str = ""
    confidence: float = 0.0
    needs_human: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "DONE"          # DONE / NEEDS_HUMAN / FAILED
    error: str = ""
    # Critic 的核对结论（默认空；`use_critic=True` 才有 ✅）
    # 结构：{"verdict": OK/WEAK/WRONG, "problems": [...], "reason": "..."}
    critique: dict = field(default_factory=dict)


JUDGE_SYSTEM = (
    "你是电商运营的决策助手。下面会给你一个差评问题簇的证据，以及**业务规则**。\n"
    "请判断：这些证据支持对这个问题采取什么处置？\n"
    "只输出一个 JSON，不要任何其它文字：\n"
    '{"recommendation": "一句话建议（要具体到做什么）",\n'
    ' "tool": "review.draft_reply 或 listing.update_desc 或 hitl.create",\n'
    ' "evidence_ids": ["E1", "E3"],\n'
    ' "confidence": 0.0~1.0,\n'
    ' "follows_rule": true 或 false,\n'
    ' "why_not_rule": "当 follows_rule=false 时必填：为什么你认为规则在这里不适用"}\n'
    "要求：\n"
    "1) evidence_ids 只能引用我给你的证据编号，**不许编造**；\n"
    '2) 如果证据不足以判断，输出 {"needs_human": true, "reason": "为什么不够"}；\n'
    "3) tool 只能从上面三个里选。\n"
    "4) **业务规则优先**：如果规则给出了这类问题该用什么工具，你应该遵守；\n"
    "   只有在证据明显说明规则不适用时，才可以选别的，并**必须**在 why_not_rule 里说清理由。\n"
    "   （规则来自业务方的人工判断，不是从数据里学的 —— 它代表'运营真正会怎么做'）"
)


def _rule_block(topic: str) -> str:
    """把 `TOOL_BY_TOPIC` 里和这个 topic 相关的一条规则，拼成给模型看的约束。

    为什么要给规则（2026-10-31 实测发现的问题）：
        第一次真模型跑 agent，模型对"掉色严重"（quality）建议 **改详情页描述**，
        而 `TOOL_BY_TOPIC["quality"]` 是 **回复 + 反馈工厂** ——
        因为"真质量问题改描述是粉饰，不解决问题" ✅
        → 那是**业务判断**，不是常识；不告诉模型，它就按常识选 ✅ ✗

    为什么只给"这一条"而不是整张表：
        给整张表会让模型看到"其它类型该用什么"，容易串（它可能把 logistics 的规则
        套到 quality 上）。只给它这个 topic 的那一条，最不容易混淆 ✅
    """
    from hub.action.proposer import TOOL_BY_TOPIC
    tool = TOOL_BY_TOPIC.get(topic or "")
    if not tool:
        return f"业务规则：问题类型 {topic!r} 没有预设规则，请根据证据判断。"
    # 规则背后的理由（和 proposer.py 的注释一致）
    why = {
        "review.draft_reply": "这类问题（面料/工艺/物流/服务）是**真实缺陷或真实体验**，"
                              "改详情页描述是粉饰，不解决问题；正确做法是回复差评管理预期 + 反馈内部",
        "listing.update_desc": "这类问题（尺码表没写清/色差没提示/描述缺信息）"
                               "是**描述本身缺信息**，补详情页能直接减少误解",
        "hitl.create": "这类问题需要人工介入判断",
    }.get(tool, "")
    return (
        f"业务规则（针对问题类型 {topic}）：应该用 **{tool}**。\n"
        f"理由：{why}\n"
        "你可以不同意，但必须在 why_not_rule 里说明为什么这条规则在这里不适用。"
    )


def _run_tool(name: str, args: dict, ctx: CallContext,
              handlers: dict) -> tuple[dict, Step]:
    """跑一个只读工具并记一条轨迹。"""
    t0 = time.time()
    idx = 0
    try:
        out = handlers[name](args, ctx)
        step = Step(idx=idx, tool=name, args=args, ok=True,
                    chars=len(str(out)), ms=int((time.time() - t0) * 1000))
        return out, step
    except Exception as e:                       # noqa: BLE001
        step = Step(idx=idx, tool=name, args=args, ok=False,
                    ms=int((time.time() - t0) * 1000), error=str(e)[:200])
        raise


def _parse_judge(text: str) -> dict | None:
    """从模型输出里抠 JSON（和探针同一套容错：允许包 ```json``` 或加解释）。"""
    import json
    import re
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def handle(cluster_id: str, *, actor_role: str = "operator",
           llm=None, ctx: CallContext | None = None,
           use_rule: bool = True, use_critic: bool = False) -> HandleResult:
    """处理一个问题簇：查证据 → 模型判断 → 出方案。

    :param cluster_id: 簇 UUID（**由调用方给，不由模型生成**）
    :param actor_role: operator / supervisor（**不要传 system** —— 那会绕过审批红线）
    :param llm:        可注入（测试用假模型）；None 则用配置里的
    :param ctx:        工具上下文（默认按 actor_role 造一个）
    :param use_rule:   是否把业务规则注入 prompt。
        **默认 True**（生产就应该给规则：它是业务方的人工判断 ✅）。
        `False` 只用于**对照实验** —— 测"模型自己的判断和规则差多少"：
            有规则组的一致率 = "它会不会听话"
            无规则组的分歧率 = "它独立判断的水平"  ← 这才是 agent 的新增信息
        （否则会得出"分歧率 0% → agent 没提供新信息"的错误结论，
          因为规则是我们写进 prompt 的 ✅）
    """
    from hub.tools.builtin import register_all
    register_all()

    ctx = ctx or CallContext(role=actor_role, trace_id=f"agent-{uuid4().hex[:8]}")
    goal = f"处理问题簇 {cluster_id}：给出处置建议"
    res = HandleResult(cluster_id=cluster_id, goal=goal)

    handlers = {
        "query_cluster": _query_cluster,
        "query_reviews": _query_reviews,
        "query_history": _query_history,
    }

    # ---------------- ①②③ 三步：参数全部由代码给 ----------------
    try:
        c, s = _run_tool("query_cluster", {"cluster_id": cluster_id}, ctx, handlers)
        s.idx = 1
        res.steps.append(s)
        # 证据编号 E1..En：**这是"可追溯"的骨架**，模型只能引用这些编号
        res.evidence.append({"source": "query_cluster", "ref": "E1",
                             "text": f"簇「{c['label']}」{c['review_count']} 条差评，"
                                     f"平台 {c['platforms']}，优先级 {c['priority_tier']}，"
                                     f"问题类型 {c['topic']}"})
        topic = c.get("topic") or ""
        res.topic = topic                      # ← 存到对象上（图版也这么读 ✅）

        r, s = _run_tool("query_reviews", {"cluster_id": cluster_id, "n": MAX_REVIEWS},
                         ctx, handlers)
        s.idx = 2
        res.steps.append(s)
        for i, rev in enumerate(r.get("reviews") or [], start=2):
            res.evidence.append({"source": "query_reviews", "ref": f"E{i}",
                                 "text": f"[{rev['platform']}] {rev['text']}"})

        h, s = _run_tool("query_history", {"topic": topic}, ctx, handlers)
        s.idx = 3
        res.steps.append(s)
        for rec in (h.get("tools") or [])[:3]:
            res.evidence.append({"source": "query_history", "ref": "E-H",
                                 "text": f"历史 {rec['tool']}：{rec['summary']}"})
    except Exception as e:                       # noqa: BLE001
        res.status = "FAILED"
        res.error = f"取证失败：{type(e).__name__}: {e}"
        return res

    # ---------------- ④ 唯一一次模型调用（抽成共享函数，见 judge_step）----------------
    res = judge_step(res, llm, use_rule=use_rule)

    # ---------------- ⑤ Critic：第二个模型独立核对（默认关，`--critic` 开）----------------
    # **不默认开**的理由：它多一次 LLM 调用（3 秒 → 6 秒）✅
    # 而"什么时候值得多花那 3 秒"应该由人决定（比如 P0 簇、要提交的提案）✅
    if use_critic and res.status == "DONE":
        from hub.agent.critic import critique as run_critic
        c = run_critic(res, llm=llm)
        res.critique = {"verdict": c.verdict, "problems": c.problems,
                        "reason": c.reason, "ok": c.ok, "error": c.error}
        res.evidence.append({
            "source": "critic", "ref": "E-CRITIC",
            "text": f"Critic：{c.verdict}"
                    + (f"　问题：{'；'.join(c.problems)}" if c.problems else "")
                    + (f"　理由：{c.reason}" if c.reason else "")
                    + (f"　（解析失败：{c.error[:60]}）" if not c.ok else ""),
        })
    return res


def judge_step(res: HandleResult, llm=None, *, use_rule: bool = True) -> HandleResult:
    """**第 ④ 步：模型判断**（手写版和图版共用这一份）。

    为什么抽出来（2026-10-31，阶段 3.3 的债）：
        `graph.py` 的 `n_judge()` 原来**复制了这段逻辑**（约 60 行）✅ ✗
        → 同一份判据两处维护 → 改 prompt 时只改一处 → **两版行为慢慢分叉** ✅
        → 而"两版等价 22/22"正是阶段 3.3 的核心结论 → 一分叉结论就失效 ✅
        → 所以必须抽成一份，两版都调它 ✅

    这段做的事（顺序不能换）：
        ① 拼 prompt（任务 + 规则块 + 证据）
        ② 调模型 → 记 Step（含 prompt_chars，供对照脚本检查两版是否一致 ✅）
        ③ 解析 JSON → 失败转人工（不猜）
        ④ 模型说"证据不足" → 转人工
        ⑤ 工具不在允许集合 → 不采用，转人工
        ⑥ **校验引用**：编造的列出来（E-VERIFY），一条有效都没有 → 转人工
        ⑦ **和业务规则对比**（E-RULE）：一致/不一致（附模型理由）/无规则
    """
    if llm is None:
        from hub.llm import get_text_llm
        llm = get_text_llm()

    topic = res.topic or ""
    evidence_text = "\n".join(f"{e['ref']}：{e['text']}" for e in res.evidence)
    prompt = (
        f"任务：{res.goal}\n\n"
        + (f"{_rule_block(topic)}\n\n" if use_rule else "")
        + f"证据（**只能引用下面的编号**）：\n{evidence_text}\n\n"
        "请判断这些证据支持什么处置。"
    )
    step = Step(idx=4, tool="llm.judge", args={"prompt_chars": len(prompt)}, ok=True)
    t0 = time.time()
    try:
        raw = llm.chat(prompt, system=JUDGE_SYSTEM, temperature=0.0, max_tokens=300)
        step.chars = len(raw)
        step.ms = int((time.time() - t0) * 1000)
        res.steps.append(step)
    except Exception as e:                       # noqa: BLE001
        step.ok = False
        step.error = str(e)[:200]
        step.ms = int((time.time() - t0) * 1000)
        res.steps.append(step)
        res.status = "FAILED"
        res.error = f"模型调用失败：{type(e).__name__}: {e}"
        return res

    judged = _parse_judge(raw)
    if judged is None:
        # 解析失败 → 转人工（**不猜**，也不假装成功 ✅）
        res.status = "NEEDS_HUMAN"
        res.needs_human = True
        res.recommendation = "模型输出无法解析，需人工判断"
        res.error = f"无法解析模型输出：{raw[:120]}"
        return res

    if judged.get("needs_human"):
        res.status = "NEEDS_HUMAN"
        res.needs_human = True
        res.recommendation = str(judged.get("reason") or "证据不足，需人工判断")[:200]
        return res

    tool = str(judged.get("tool") or "")
    if tool not in ALLOWED_TOOLS:
        # 模型给了个不在集合里的工具 → **不采用**（但保留建议原文供人工看）
        res.status = "NEEDS_HUMAN"
        res.needs_human = True
        res.recommendation = f"模型建议的工具 {tool!r} 不在允许集合内：{str(judged.get('recommendation'))[:160]}"
        return res

    # ---------------- 校验：引用的证据必须存在 ----------------
    valid_refs = {e["ref"] for e in res.evidence}
    cited = [str(x) for x in (judged.get("evidence_ids") or [])]
    ok_cited = [c for c in cited if c in valid_refs]
    dropped = [c for c in cited if c not in valid_refs]

    res.tool = tool
    res.recommendation = str(judged.get("recommendation") or "")[:300]
    try:
        res.confidence = max(0.0, min(1.0, float(judged.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        res.confidence = 0.0

    # ---------------- 和业务规则对比（**分歧要显式记录**） ----------------
    # 为什么要记：规则是业务方的人工判断（"质量问题改描述是粉饰"），
    # 模型是常识判断。两者不一致时：
    #   · 可能是模型错了（那就由规则兜底）
    #   · 也可能模型看到了规则没覆盖的情况（那就值得人工看）
    # → **不管哪种，都必须让人看见分歧**，不能悄悄发生 ✅
    from hub.action.proposer import TOOL_BY_TOPIC
    rule_tool = TOOL_BY_TOPIC.get(topic or "")
    follows = (tool == rule_tool) if rule_tool else None
    why_not = str(judged.get("why_not_rule") or "")[:200]
    if follows is True:
        rule_note = f"与业务规则一致（{topic} → {rule_tool}）"
    elif follows is False:
        # `--no-rule` 对照模式下模型**不知道规则** → "没给理由"是正常的，不算它的错
        if why_not:
            tail = f"；模型理由：{why_not}"
        elif use_rule:
            tail = "；**模型没给理由**"
        else:
            tail = "（对照组：未向模型提供规则，故不要求理由）"
        rule_note = f"**与业务规则不一致**：规则是 {rule_tool}，模型选了 {tool}{tail}"
    else:
        rule_note = f"这个 topic（{topic}）没有预设规则，模型自行判断"
    res.evidence.append({"source": "rule", "ref": "E-RULE", "text": rule_note})

    # 引用被丢弃的情况要**留痕**（这是"模型编造引用"的证据）
    res.evidence.append({
        "source": "judge",
        "ref": "E-VERIFY",
        "text": f"引用了 {len(cited)} 条证据，有效 {len(ok_cited)} 条"
                + (f"，**编造 {len(dropped)} 条**（{','.join(dropped[:5])}）" if dropped else ""),
    })

    # 一条有效引用都没有 → 结论没有依据 → 转人工（宁可这样，也不给没依据的建议）
    if not ok_cited:
        res.status = "NEEDS_HUMAN"
        res.needs_human = True
        res.recommendation = f"（无有效依据）{res.recommendation}"
        return res

    res.status = "DONE"
    return res


def save(res: HandleResult, *, proposal_id: UUID | None = None) -> UUID:
    """把一次 agent 任务落库，返回 agent_run.id。

    调用方（CLI / API）负责传 `proposal_id`（采纳后回填）。
    """
    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun

    with get_session() as s:
        row = AgentRun(
            cluster_id=UUID(res.cluster_id),
            trace_id=f"agent-{uuid4().hex[:8]}",
            actor_role="operator",
            status=res.status,
            goal=res.goal,
            steps=[{"idx": st.idx, "tool": st.tool, "args": st.args, "ok": st.ok,
                    "chars": st.chars, "ms": st.ms, "error": st.error}
                   for st in res.steps],
            evidence=res.evidence,
            result={"recommendation": res.recommendation, "tool": res.tool,
                    "confidence": res.confidence, "needs_human": res.needs_human},
            tokens_in=res.tokens_in or None,
            tokens_out=res.tokens_out or None,
            proposal_id=proposal_id,
            error=res.error or None,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
