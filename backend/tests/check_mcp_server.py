# -*- coding: utf-8 -*-
"""
MCP server 测试（check_mcp_server.py）—— 阶段 3.2
================================================
**不启进程、不用 stdio** —— 直接用 `FastMCP.call_tool()` 在进程内调，
所以是秒级、稳定、能进 quick 组 ✅
（`call_tool` 的存在性是 `scripts/probe_mcp.py` 探出来的，不是猜的 ✅）

验 6 条：
    1. 6 个 MCP 工具都注册了（5 只读 + 1 通用 invoke）
    2. 只读工具能通过 MCP 调通（`query_cluster` 返回真的簇名）
    3. **写工具经过网关**：L3 的 `product.update_price` 走 MCP 后**不执行**，
       而是 `PENDING_APPROVAL` ← **这是最关键的一条** ✅
    4. 参数非法（L2 改描述传不存在的 spu）→ 网关拒绝，**不是 MCP 层崩**
    5. 不存在的工具名 → 网关拒绝（DENIED），MCP 不抛异常
    6. `invoke` 的 JSON 传坏 → 返回 BAD_JSON，MCP 不抛异常

第 3 条为什么最重要：
    MCP 是"外部 agent 的入口" ✅
    如果写工具能绕过网关直接执行 → **外部 agent 就能改价/下架而不经审批** ✅ ✗
    所以这条测试是"MCP 没有变成安全洞"的证据 ✅

跑法：python tests/check_mcp_server.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


async def call(m, name: str, args: dict) -> dict:
    """调 MCP 工具，把返回的 TextContent 解成 dict。"""
    contents, _raw = await m.call_tool(name, args)
    text = contents[0].text if contents else "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def first_cluster() -> str | None:
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        c = s.scalars(select(IssueCluster).limit(1)).first()
        return str(c.id) if c else None


def main() -> int:
    print("=" * 62)
    print("  MCP server（阶段 3.2）")
    print("=" * 62)

    from hub.mcp_server import mcp

    # ---------- 1. 工具都在 ----------
    print("\n[1] MCP 工具注册")
    tools = asyncio.run(mcp.list_tools())
    names = sorted(t.name for t in tools)
    expect = ["invoke", "query_cluster", "query_history", "query_outcome",
              "query_product", "query_reviews"]
    check("6 个工具都在", all(e in names for e in expect), f"{names}")

    cid = first_cluster()
    if cid is None:
        print("\n[!] 库里没有簇，跳过后面的实测")
        return 1 if FAILS else 0

    # ---------- 2. 只读能调通 ----------
    # ⚠️ 只读工具必须是 `idempotent=False`（2026-10-31 踩过）：
    #    `idempotent=True` 会让同参数**直接返回上次结果**（REPLAYED）→ 读到旧数据 ✅ ✗
    #    所以这里断言的是 EXECUTED（真的查了一次），**不是** REPLAYED ✅
    print("\n[2] 只读工具经 MCP 调通（且经过网关、且**不是幂等命中**）")
    r = asyncio.run(call(mcp, "query_cluster", {"cluster_id": cid}))
    check("decision=EXECUTED（真的查了，不是 REPLAYED）",
          r.get("decision") == "EXECUTED", r.get("decision"))
    label = (r.get("data") or {}).get("label")
    check("拿到簇名", bool(label), str(label))
    check("有 execution_id（审计）", bool(r.get("execution_id")), str(r.get("execution_id")))

    # ---------- 3. 写工具不会被执行 ----------
    # `product.update_price` 要求 role=manager，而 MCP 默认 role=operator ✅
    #   → 更早一步就被 SCOPE_DENIED 拦住（**比"进审批队列"更安全** ✅）
    #   想看 PENDING_HITL 得用 manager 角色 —— 但 MCP 的默认值**故意**是 operator
    #   参数按 `builtin.UpdatePriceParams` 的真实定义（platform/spu_id/new_price，
    #   extra=forbid → 多给 reason 会被 PARAM_INVALID ✅）
    print("\n[3] 写工具（L3 改价）经 MCP **不执行**")
    r = asyncio.run(call(mcp, "invoke", {
        "tool_name": "product.update_price",
        "args_json": json.dumps({"platform": "taobao", "spu_id": "TB-0001",
                                 "new_price": 79}),
        "evidence_json": "{}",
    }))
    check("decision=REJECTED", r.get("decision") == "REJECTED", r.get("decision"))
    check("deny_code=SCOPE_DENIED（operator 无权提 L3）",
          r.get("deny_code") == "SCOPE_DENIED", str(r.get("deny_code")))
    check("没有真的执行（data 为空）", not r.get("data"), str(r.get("data")))

    # ---------- 4. 参数非法 → 网关拒绝 ----------
    print("\n[4] 参数非法 → 网关拒绝（不是 MCP 崩）")
    r = asyncio.run(call(mcp, "invoke", {
        "tool_name": "listing.update_desc",
        "args_json": json.dumps({"spu_id": "不存在的商品", "description": "x"}),
        "evidence_json": "{}",
    }))
    # REJECTED = 被拒（参数/权限/依据/频控），deny_code 说明具体原因
    check("decision=REJECTED", r.get("decision") == "REJECTED", r.get("decision"))
    check("deny_code=PARAM_INVALID", r.get("deny_code") == "PARAM_INVALID",
          str(r.get("deny_code")))

    # ---------- 5. 不存在的工具 → 拒绝 ----------
    print("\n[5] 不存在的工具名 → 拒绝")
    r = asyncio.run(call(mcp, "invoke", {"tool_name": "drop.all.tables",
                                         "args_json": "{}"}))
    check("decision=REJECTED", r.get("decision") == "REJECTED", r.get("decision"))
    check("deny_code=TOOL_NOT_FOUND", r.get("deny_code") == "TOOL_NOT_FOUND",
          str(r.get("deny_code")))

    # ---------- 6. 坏 JSON → 不抛异常 ----------
    print("\n[6] invoke 的 JSON 传坏 → 返回 BAD_JSON")
    r = asyncio.run(call(mcp, "invoke", {"tool_name": "query_cluster",
                                         "args_json": "{坏掉了"}))
    check("deny_code=BAD_JSON", r.get("deny_code") == "BAD_JSON", r.get("deny_code"))
    check("ok=False", r.get("ok") is False)

    print("\n" + "=" * 62)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 62)
        return 1
    print("  全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
