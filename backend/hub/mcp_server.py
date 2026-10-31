# -*- coding: utf-8 -*-
"""
MCP server（mcp_server.py）—— 阶段 3.2
=====================================
把我们的工具按 **MCP（Model Context Protocol）** 暴露出去，让**外部 agent**
（Claude Desktop / 别人的 LangGraph 应用）也能用。

════════════════════════════════════════════════════════════════════
⚠️ **最重要的一条：MCP 只是"传输"，不是"执行路径"**
════════════════════════════════════════════════════════════════════
每个 MCP 工具的实现都是**一个薄壳**：

    MCP 调用 → 构造 `ToolCall` → **`hub.gateway.invoke(...)`** → 转成 MCP 返回

**绝不**在 MCP 层直接调 `builtin.py` 的 handler 或 `readonly.py` 的函数 ✅
否则外部 agent 调 MCP 就等于**绕过四道防线**（参数校验 / 依据 / 幂等 / 频控）
和风险分级 ✅ ✗

这样做的直接后果（也是这条设计的意义）：
    · 外部 agent 调 `product.update_price`（L3）→ **不会执行**，
      而是生成待审批工单 ✅
    · 频控、幂等、参数校验**全部照旧** ✅
    · 审计（`tool_execution`）里能看到"这次是 MCP 调进来的" ✅

════════════════════════════════════════════════════════════════════
暴露哪些工具
════════════════════════════════════════════════════════════════════
· **5 个只读工具**：逐个写薄壳（参数 schema 明确，外部调用方便 ✅）
· **写工具**：一个通用 `invoke` 工具（`tool_name` + `args`）——
  因为它们的参数 schema 复杂且会变，硬编码成 MCP 参数不划算；
  但**照样走网关**，所以安全性不打折 ✅

跑法：
    python -m hub.mcp_server              # stdio（给 Claude Desktop 之类用）
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

# stdio transport 由 FastMCP 自己处理（run_stdio_async）
mcp = FastMCP("e-commerce-operations-hub")

# MCP 调进来的角色：默认 operator（**不是 system** —— 那会绕过 L2/L3 审批 ✅）
DEFAULT_ROLE = "operator"


def _invoke(tool_name: str, args: dict, evidence_refs: dict | None = None) -> dict:
    """**所有 MCP 工具的公共出口**：走网关，不绕过任何防线。

    :return: 一个 JSON 可序列化的 dict（给 MCP 客户端看）：
        {
          "decision": "EXECUTED" / "PENDING_APPROVAL" / "DENIED" / "REPLAYED",
          "ok": bool,
          "data": 工具的返回（或 None）,
          "deny_code": 被拒的原因码（如 PARAM_INVALID / RISK_TOO_HIGH）,
          "detail": 补充信息,
          "execution_id": 审计 id
        }
    """
    from hub.gateway import invoke
    from hub.gateway.schemas import Decision, ToolCall
    from hub.tools.builtin import register_all
    from hub.tools.spec import CallContext

    register_all()          # 幂等
    call = ToolCall(tool_name=tool_name, args=args or {},
                    evidence_refs=evidence_refs or {})
    ctx = CallContext(role=DEFAULT_ROLE, trace_id=f"mcp-{tool_name}")
    res = invoke(call, ctx)

    ok = res.decision in (Decision.EXECUTED, Decision.REPLAYED)
    return {
        "decision": getattr(res.decision, "value", str(res.decision)),
        "ok": ok,
        "data": res.response,
        "deny_code": res.deny_code,
        "detail": res.detail,
        "execution_id": res.execution_id,
    }


def _dump(d: dict) -> str:
    """统一转成字符串（MCP 的返回是文本 ✅；结构化数据放 JSON 里）。"""
    return json.dumps(d, ensure_ascii=False, default=str)


# ════════════════════════════════════════════════════════════ 只读工具
@mcp.tool()
def query_cluster(cluster_id: str) -> str:
    """查一个差评问题簇的概况：名字、条数、平台、优先级、品类、问题类型。

    :param cluster_id: 簇的 UUID（由调用方给出）
    """
    return _dump(_invoke("query_cluster", {"cluster_id": cluster_id}))


@mcp.tool()
def query_reviews(cluster_id: str, n: int = 5) -> str:
    """查一个簇里的代表评价原文（最多 5 条，每条截断到 120 字）。

    :param cluster_id: 簇的 UUID
    :param n: 要几条（1~5）
    """
    return _dump(_invoke("query_reviews", {"cluster_id": cluster_id, "n": n}))


@mcp.tool()
def query_history(topic: str, tool: str = "") -> str:
    """查历史上同一类问题的处置方式和结果（有效/有害/不明确各几次）。

    :param topic: 问题类型，如 quality / size / logistics
    :param tool: 只看某个工具的历史（空=全部）
    """
    args: dict[str, Any] = {"topic": topic}
    if tool:
        args["tool"] = tool
    return _dump(_invoke("query_history", args))


@mcp.tool()
def query_product(spu_id: str, days: int = 30) -> str:
    """查一个商品的标题/品类 + 最近 N 天的销售聚合（GMV/件数/退款率）。

    :param spu_id: 平台商品 ID
    :param days: 统计最近多少天（7~90）
    """
    return _dump(_invoke("query_product", {"spu_id": spu_id, "days": days}))


@mcp.tool()
def query_outcome(proposal_id: str) -> str:
    """查一个提案执行后的复盘结论（DID / 置信区间 / 结论 / 无法判定的原因）。

    :param proposal_id: 提案 UUID
    """
    return _dump(_invoke("query_outcome", {"proposal_id": proposal_id}))


# ════════════════════════════════════════════════════════════ 写工具（通用入口）
@mcp.tool()
def invoke(tool_name: str, args_json: str = "{}", evidence_json: str = "{}") -> str:
    """调用任意**已注册**的工具（含写工具）。**照样走网关的四道防线和风险分级**。

    为什么写工具用通用入口而不是逐个暴露：
        它们的参数 schema 复杂且会变（改描述要 spu_id + 新描述；改价要 spu_id +
        新价 + 理由），硬编码成 MCP 参数不划算 ✅
        而且**安全性完全不打折** —— 统一走 `gateway.invoke` ✅

    ⚠️ L2/L3 的动作**不会执行**：会返回 `decision=PENDING_APPROVAL` + 一个待审批任务。
    这是设计如此，不是失败 ✅

    :param tool_name: 工具名（如 listing.update_desc / product.update_price）
    :param args_json: 参数的 JSON 字符串
    :param evidence_json: 依据的 JSON 字符串（如 {"issue_cluster": "<uuid>"}）
    """
    try:
        args = json.loads(args_json or "{}")
        evidence = json.loads(evidence_json or "{}")
    except json.JSONDecodeError as e:
        return _dump({"decision": "DENIED", "ok": False,
                      "deny_code": "BAD_JSON", "detail": {"error": str(e)}})
    return _dump(_invoke(tool_name, args, evidence))


# ════════════════════════════════════════════════════════════ 入口
def main() -> None:
    """stdio 入口（MCP 客户端通过子进程 stdio 连它）。"""
    import asyncio
    print("MCP server 启动（stdio）……", file=__import__("sys").stderr)
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
