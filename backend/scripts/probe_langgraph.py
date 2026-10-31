# -*- coding: utf-8 -*-
"""
LangGraph API 探针（probe_langgraph.py）—— 阶段 3.3 的前置
==========================================================
**为什么要有这个**：写 LangGraph 版本之前，必须先确认 **1.2.17 的真实 API**，
不能按记忆写（0.x 到 1.x 有过破坏性变更）。

这个探针做三件事：
    ① 打印版本 + `langgraph.graph` 的公开名字（看 `StateGraph` / `START` / `END` 在不在）
    ② 打印 `StateGraph` 的方法列表（看 `add_node` / `add_edge` / `compile` 的签名痕迹）
    ③ **跑一个最小可用的图**（2 个节点 + 1 条边 + 1 条条件边）
       —— 它能跑通，就说明 API 是我想的那个样子 ✅
       —— 它跑不通，报错信息本身会告诉我正确用法 ✅

跑法：python scripts/probe_langgraph.py
"""
import sys
from typing import TypedDict


def main() -> int:
    print("=" * 60)
    print("  LangGraph API 探针")
    print("=" * 60)

    try:
        import langgraph
    except ImportError:
        print("[X] langgraph 没装。pip install langgraph")
        return 1

    print(f"\n[1] 版本：{getattr(langgraph, '__version__', '(无 __version__)')}")

    try:
        import langgraph.graph as g
        names = [n for n in dir(g) if not n.startswith("_")]
        print(f"[2] langgraph.graph 公开名字：{names}")
    except Exception as e:                    # noqa: BLE001
        print(f"[2] 导入 langgraph.graph 失败：{type(e).__name__}: {e}")
        return 1

    try:
        from langgraph.graph import END, START, StateGraph
        print("[3] `from langgraph.graph import StateGraph, START, END` ✅ 可用")
    except Exception as e:                    # noqa: BLE001
        print(f"[3] 导入 StateGraph/START/END 失败：{type(e).__name__}: {e}")
        return 1

    methods = [m for m in dir(StateGraph) if not m.startswith("_")]
    print(f"[4] StateGraph 方法：{methods}")

    # ---------------- 最小可用的图 ----------------
    print("\n[5] 跑一个最小图（2 节点 + 条件边）")

    class S(TypedDict):
        n: int
        log: list[str]

    def node_a(state: S) -> dict:
        return {"n": state["n"] + 1, "log": state["log"] + ["a"]}

    def node_b(state: S) -> dict:
        return {"n": state["n"] * 10, "log": state["log"] + ["b"]}

    def route(state: S) -> str:
        # 条件边：n < 5 走 b，否则结束
        return "b" if state["n"] < 5 else END

    try:
        g = StateGraph(S)
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_conditional_edges("a", route, {"b": "b", END: END})
        g.add_edge("b", END)
        app = g.compile()
        out = app.invoke({"n": 0, "log": []})
        print(f"  [OK] 图跑通了：{out}")
        ok = out["n"] == 10 and out["log"] == ["a", "b"]
        print(f"  {'[OK]' if ok else '[FAIL]'} 结果符合预期（n=10, log=[a,b]）")
    except Exception as e:                    # noqa: BLE001
        import traceback
        print(f"  [X] 图跑不通：{type(e).__name__}: {e}")
        print("  --- 完整堆栈（按这个改代码，不要按记忆改）---")
        traceback.print_exc()
        return 1

    print("\n" + "=" * 60)
    print("  结论：上面 [3][4][5] 的写法**就是可用 API** —— 按它写 graph.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
