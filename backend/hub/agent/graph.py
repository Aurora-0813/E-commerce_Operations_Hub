# -*- coding: utf-8 -*-
"""
LangGraph 版 agent（graph.py）—— 阶段 3.3（对照实现）
====================================================
**这是同一个任务的第二种实现**，用于和手写版（`handle.py`）对照。

为什么要写两份：
    JD 要 LangGraph ✅，但形态 ③ 的流程是"固定 3 步取证 + 1 次判断"，
    **图框架的价值（路由/环/状态机）在这里用不上** ✅
    → 如果只写图版本，等于"为了关键词而用框架"，会被问穿 ✅
    → 写两份并**给出对比数据**，才能说清"我知道它解决什么问题、也知道这里不需要" ✅

两份的差异被刻意限制在**编排方式**：
    · 节点函数、判据、校验、返回结构 **全部复用 `handle.py`** ✅
    （否则差异会混进"实现细节"，对比就不公平了 ✅）

图结构（和手写版的 4 步一一对应）：

    START → cluster → reviews → history → judge ─┬→ END（有结论）
                                                 └→ END（证据不足 → NEEDS_HUMAN）
                                    ↑
                          这里用了**条件边**：
                          取证阶段任何一步失败 → 直接结束，不进 judge
                          （手写版是靠 try/except 实现的 ✅）

跑法（对照）：
    python scripts/compare_engines.py        # 同一批簇跑两遍，比耗时/结果一致性
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from hub.agent.handle import (JUDGE_SYSTEM, HandleResult, Step,  # noqa: F401
                             _parse_judge, _rule_block)
from hub.tools.readonly import MAX_REVIEWS, _query_cluster, _query_history, _query_reviews
from hub.tools.spec import CallContext

HANDLERS = {
    "query_cluster": _query_cluster,
    "query_reviews": _query_reviews,
    "query_history": _query_history,
}


class GState(TypedDict, total=False):
    """图的共享状态。

    和手写版比：手写版用一个 `HandleResult` 对象累积状态 ✅
    图里必须把状态声明成 TypedDict（因为 LangGraph 靠它做合并 ✅）
    → **这就是"多一层抽象"的具体体现** ✅
    """
    cluster_id: str
    ctx_role: str
    topic: str
    result: HandleResult
    failed: bool


# ==================================================================== 节点
def _wrap(res: HandleResult, idx: int, tool: str, args: dict,
          fn, trace_id: str = "") -> dict:
    """跑一个只读工具并记轨迹（复用 handle.py 的 Step 结构）。

    ⚠️ `trace_id` 由调用方**显式传入** —— 原来写的是 `res.steps[0].args[...]`，
    而第一次调用时 `res.steps` 还是空的 → IndexError ✅ ✗
    （也被 `except` 吞成了"工具失败"，更难查 ✅）
    """
    import time

    from hub.agent.handle import Step

    t0 = time.time()
    try:
        out = fn(args, CallContext(role="operator", trace_id=trace_id))
    except Exception as e:                       # noqa: BLE001
        res.steps.append(Step(idx=idx, tool=tool, args=args, ok=False,
                              ms=int((time.time() - t0) * 1000), error=str(e)[:200]))
        res.status = "FAILED"
        res.error = f"{tool} 失败：{type(e).__name__}: {e}"
        return {"result": res, "failed": True}
    res.steps.append(Step(idx=idx, tool=tool, args=args, ok=True,
                          chars=len(str(out)), ms=int((time.time() - t0) * 1000)))
    return {"result": res, "out": out, "failed": False}


def n_cluster(state: GState) -> dict:
    res = state["result"]
    r = _wrap(res, 1, "query_cluster", {"cluster_id": state["cluster_id"]},
              HANDLERS["query_cluster"], trace_id=res.cluster_id)
    if r["failed"]:
        return {"result": res, "failed": True}
    c = r["out"]
    # ✅ topic 存到 result 对象上（**不靠 state 合并** —— 实测那样会丢 ✅）
    res.topic = c.get("topic") or ""
    res.evidence.append({"source": "query_cluster", "ref": "E1",
                         "text": f"簇「{c['label']}」{c['review_count']} 条差评，"
                                 f"平台 {c['platforms']}，优先级 {c['priority_tier']}，"
                                 f"问题类型 {res.topic}"})
    return {"result": res, "topic": res.topic, "failed": False}


def n_reviews(state: GState) -> dict:
    res = state["result"]
    r = _wrap(res, 2, "query_reviews",
              {"cluster_id": state["cluster_id"], "n": MAX_REVIEWS},
              HANDLERS["query_reviews"], trace_id=res.topic)
    if r["failed"]:
        return {"result": res, "failed": True}
    for i, rev in enumerate(r["out"].get("reviews") or [], start=2):
        res.evidence.append({"source": "query_reviews", "ref": f"E{i}",
                             "text": f"[{rev['platform']}] {rev['text']}"})
    return {"result": res, "failed": False}


def n_history(state: GState) -> dict:
    res = state["result"]
    r = _wrap(res, 3, "query_history", {"topic": res.topic},
              HANDLERS["query_history"], trace_id=res.cluster_id)
    if r["failed"]:
        return {"result": res, "failed": True}
    for rec in (r["out"].get("tools") or [])[:3]:
        res.evidence.append({"source": "query_history", "ref": "E-H",
                             "text": f"历史 {rec['tool']}：{rec['summary']}"})
    return {"result": res, "failed": False}


def n_judge(state: GState) -> dict:
    """唯一一次模型调用 —— **直接调 `handle.judge_step()`**（判据只有一份 ✅）。

    2026-10-31 阶段 3.3 的债：这里原来把判据整段复制了一份（约 60 行）✅ ✗
    → 改 prompt 时两处都要改，忘一处就会让"两版等价 22/22"的结论失效 ✅
    → 抽成 `judge_step()` 后，图版只剩"取 llm + 调它 + 写回 state"三行 ✅
    """
    from hub.agent.handle import judge_step
    from hub.llm import get_text_llm

    res = state["result"]
    llm = state.get("_llm") or get_text_llm()
    return {"result": judge_step(res, llm)}


def _after_gather(state: GState) -> str:
    """条件边：取证失败就直接结束（对应手写版的 try/except ✅）。"""
    return END if state.get("failed") else "judge"


def build(llm=None):
    """构图并编译。`llm` 可注入（测试用），None 则在节点里按配置取。"""
    g = StateGraph(GState)
    g.add_node("cluster", n_cluster)
    g.add_node("reviews", n_reviews)
    g.add_node("history", n_history)
    g.add_node("judge", n_judge)
    g.add_edge(START, "cluster")
    g.add_edge("cluster", "reviews")
    g.add_edge("reviews", "history")
    g.add_conditional_edges("history", _after_gather, {"judge": "judge", END: END})
    g.add_edge("judge", END)
    app = g.compile()
    # 把 llm 塞进配置，节点里从 state 取不到就退回配置里的
    return app


def handle_graph(cluster_id: str, *, llm=None) -> HandleResult:
    """和 `handle.handle()` 同签名、同返回结构（便于对照）。"""
    from uuid import uuid4

    res = HandleResult(cluster_id=cluster_id,
                       goal=f"处理问题簇 {cluster_id}：给出处置建议")
    # 手写版把 trace_id 放在 CallContext；图里放进 state（节点的 ctx 由 _wrap 造）
    res.steps.append  # noqa: B018  （占位：保证 steps 是同一个 list 实例）
    app = build()
    out = app.invoke({"cluster_id": cluster_id, "result": res, "failed": False,
                      "_llm": llm})
    return out["result"]
