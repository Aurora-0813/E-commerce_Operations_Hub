# -*- coding: utf-8 -*-
"""
清理测试数据（clean_test_data.py）
=================================
**为什么需要**：`check_gateway` / `check_mcp_server` / `check_agent_handle` 等测试
会往**真实业务表**里写东西：
    · `tool_execution`（审计）—— 可以接受 ✅
    · `hitl_task`（待批任务）—— **不该有** ✅ ✗ 会污染 `hub today` 的「要我做」
    · `action_proposal`（提案）—— **更不该有** ✅ ✗

实测（2026-10-31）：跑了一轮测试后，`hub today` 的「要我做」里混进了 4 条
`[L0] catalog.query_price`（L0 是只读，本来就不该审批）+ 2 条测试造的 L2。

设计原则：
    ① **只按 `trace_id` 前缀删** —— 测试都用固定前缀（`gw-test` / `mcp-` / …），
       而真实数据用的是 `adopt-` / `agent-` / 真正的 trace_id ✅
       → **绝不按"时间"或"工具名"删**（那会误伤真实数据 ✅）
    ② **默认 dry-run**，必须显式 `--apply` 才真删 ✅
    ③ 列清单 + 计数，删之前人能看到要删什么 ✅

跑法：
    python scripts/clean_test_data.py            # 只看（dry-run，默认）
    python scripts/clean_test_data.py --apply    # 真删
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, or_, select      # noqa: E402

from hub.persistence.db import get_session            # noqa: E402
from hub.persistence.models import (ActionProposal, HitlTask,  # noqa: E402
                                    ToolExecution)

# 测试用的 trace_id 前缀（**故意列全，删漏比删多好** ✅）
#
# ⚠️ 这份列表**改过一次**（2026-10-31，dry-run 救回来的）：
#    第一版里有 `submit-` 和 `agent-` —— 但**它们都是真实数据** ✅ ✗
#      · `submit-xxx`   ← `hub handle --adopt --submit` 提交网关（**真的提案** ✅）
#      · `agent-xxx`    ← `hub/agent/handle.py` 的 trace（**真的 agent 运行** ✅）
#    如果按第一版 --apply，会把刚采纳的真实提案删掉 ✅
#    → **所以现在它们进了保护列表** ✅
TEST_PREFIXES = (
    "gw-test",        # check_gateway.py
    "t-9b",           # （旧命名，保留兼容）
    "mcp-",           # MCP 调用（⚠️ 目前只有测试在用；将来真实 MCP 调用要换 trace 方案）
    "test-readonly",  # check_readonly_tools.py
)

# ⚠️ **绝对不删的前缀**（真实业务数据）
SAFE_PREFIXES = (
    "adopt-",      # hub handle --adopt 生成的提案（真实）
    "submit-",     # hub handle --adopt --submit 提交网关（真实）
    "agent-",      # agent 的一次运行（真实）
    "eval-",       # hub eval 的运行（真实）
    "propose-",    # 批处理提案（真实）
)


def _like_any(col, prefixes):
    return or_(*[col.like(f"{p}%") for p in prefixes])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删（默认只打印）")
    a = ap.parse_args()

    print("=" * 68)
    print("  清理测试数据" + ("" if a.apply else "（dry-run：只看不删）"))
    print("=" * 68)
    print(f"  测试前缀：{TEST_PREFIXES}")
    print(f"  保护前缀：{SAFE_PREFIXES}（真实数据，绝不删）\n")

    with get_session() as s:
        # ---- ① 待批任务（污染「要我做」的元凶）----
        q_task = select(HitlTask).where(_like_any(HitlTask.trace_id, TEST_PREFIXES))
        tasks = s.scalars(q_task).all()

        # ---- ② 提案（测试造出来的）----
        q_prop = select(ActionProposal).where(_like_any(ActionProposal.trace_id, TEST_PREFIXES))
        props = s.scalars(q_prop).all()

        # ---- ③ 审计记录（可选清；不清也不影响业务查询）----
        q_exec = select(ToolExecution).where(_like_any(ToolExecution.trace_id, TEST_PREFIXES))
        execs = s.scalars(q_exec).all()

        def show(name, rows, label_fn):
            print(f"  【{name}】{len(rows)} 条")
            for r in rows[:8]:
                print(f"     - {label_fn(r)}")
            if len(rows) > 8:
                print(f"     … 还有 {len(rows) - 8} 条")
            print()

        show("待批任务 hitl_task", tasks,
             lambda r: f"[{r.proposal_id}] {r.trace_id}　{r.status}")
        show("提案 action_proposal", props,
             lambda r: f"{str(r.id)[:8]}　{r.tool_name}　{r.trace_id}")
        show("审计 tool_execution", execs,
             lambda r: f"{r.tool_name}　{r.decision}　{r.trace_id}")

        if not a.apply:
            print("=" * 68)
            print("  这是 dry-run。确认上面的都能删，再加 --apply 真删。")
            # 双重保险：**逐个检查命中的行，有没有落在保护前缀里**
            bad = []
            for r in list(tasks) + list(props) + list(execs):
                tid = getattr(r, "trace_id", "") or ""
                if any(tid.startswith(p) for p in SAFE_PREFIXES):
                    bad.append(f"{type(r).__name__}: {tid}")
            if bad:
                print(f"\n  🛑 **命中 {len(bad)} 条保护数据，禁止删除！**")
                for b in bad[:10]:
                    print(f"     - {b}")
                print("     请把上面这些贴给我 —— 说明前缀判断还有问题 ✅")
                print("=" * 68)
                return 2
            print("  ✅ 没有命中任何保护前缀（adopt-/submit-/agent-/eval-/propose-）")
            print("=" * 68)
            return 0

        # ---- 真删（顺序：先子后父，避免外键）----
        n_task = s.execute(delete(HitlTask).where(
            _like_any(HitlTask.trace_id, TEST_PREFIXES))).rowcount
        n_exec = s.execute(delete(ToolExecution).where(
            _like_any(ToolExecution.trace_id, TEST_PREFIXES))).rowcount
        n_prop = s.execute(delete(ActionProposal).where(
            _like_any(ActionProposal.trace_id, TEST_PREFIXES))).rowcount
        # 顺着 proposal 删掉它们对应的 run（adopt/propose 的 run 是真实数据，这里不删 ✅）
        s.commit()

    print("=" * 68)
    print(f"  已删除：待批任务 {n_task} 条｜提案 {n_prop} 条｜审计 {n_exec} 条")
    print("  再跑 `hub today` 确认「要我做」里干净了。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
