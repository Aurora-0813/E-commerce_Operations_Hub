# -*- coding: utf-8 -*-
"""
agent 的运行约束（guard.py）—— 补 7.3/7.4 里"必须有否则烧钱"的那几项
=====================================================================
路线图 §7.3/§7.4 明确要求、但我第一版**没做**的六项，集中在这里：

| 路线图要求 | 在哪 |
| --- | --- |
| token 预算上限 | `AgentLimits.token_budget` + `token_exceeded()` |
| 超时 5 分钟 | `AgentLimits.timeout_s` + `check_deadline()` |
| 全局并发上限 2 | `AgentLimits.max_concurrent` + `acquire_slot()/release_slot()` |
| 重复提交返回已有 task_id | `find_running()` |
| 步数上限 ≤3 | `AgentLimits.max_steps` |
| Critic 连续两次不通过 → 转人工 | `apply_critic_verdict()` |

**为什么这些都集中在"钱"和"卡死"上**：
    · LLM 是**按次计费**的 ✅ → 并发不限 = 同时烧 10 份钱 ✅
    · 超时不设 = 一个卡住的请求占着资源 ✅
    · 重复提交不拦 = 同一个簇被算两遍 ✅
    · 步数不设 = 7B 会**用尽步数**（实测 6/6 走满 5 步 ✅）
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# 这些是**默认值**，不是硬编码：调用方可以传自己的 limits ✅


@dataclass(frozen=True)
class AgentLimits:
    """一次 agent 运行的资源上限。"""
    max_steps: int = 3            # **取证步数**上限（不含 judge / critic）
    timeout_s: int = 300          # 路线图：超时 5 分钟
    max_concurrent: int = 2       # 路线图：全局并发上限 2
    token_budget: int = 20000     # token 预算（一次任务）
    critic_max_reject: int = 2    # Critic 连续不通过几次 → 转人工


DEFAULT_LIMITS = AgentLimits()


class BudgetExceeded(Exception):
    """预算/超时/并发超限。**由调用方转成 FAILED 或 NEEDS_HUMAN**，不往上抛。"""


# ════════════════════════════════════════════════════ 超时
def check_deadline(started_at: float, limits: AgentLimits) -> None:
    """在**每一步之间**检查是否超时（不是靠打断线程 —— 那不可靠 ✅）。"""
    used = time.time() - started_at
    if used > limits.timeout_s:
        raise BudgetExceeded(
            f"超时：已用 {used:.0f}s，上限 {limits.timeout_s}s"
        )


# ════════════════════════════════════════════════════ token 预算
def token_exceeded(tokens_in: int, tokens_out: int, limits: AgentLimits) -> bool:
    """token 用超了吗（in + out 都算 ✅）。"""
    return (tokens_in + tokens_out) > limits.token_budget


# ════════════════════════════════════════════════════ 并发上限
def count_running() -> int:
    """当前有多少个 agent 任务在跑（`agent_run.status = RUNNING`）。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun

    with get_session() as s:
        return int(s.scalar(
            select(func.count()).select_from(AgentRun)
            .where(AgentRun.status == "RUNNING")
        ) or 0)


def acquire_slot(limits: AgentLimits = DEFAULT_LIMITS) -> None:
    """占一个并发位；超了抛 `BudgetExceeded`。

    ⚠️ 这是**先查后占**，不是原子操作 ✅ —— 高并发下可能同时通过 ✅
       为什么可接受：agent 是"人点一下跑一个"✅，并发压力来自误操作而不是流量 ✅
       真要做到原子，得用 DB 行锁/Redis 信号量 —— **当前规模不值得** ✅
       （但这句解释必须写出来，否则以后有人以为它是严格的 ✅）
    """
    n = count_running()
    if n >= limits.max_concurrent:
        raise BudgetExceeded(
            f"并发已满：{n}/{limits.max_concurrent} 个 agent 任务在跑。"
            "等它们结束，或调整 HUB 的并发上限 ✅"
        )


# ════════════════════════════════════════════════════ 提交幂等 / 已有任务
def find_running(cluster_id) -> str | None:
    """同一簇已有未完成任务吗？有就返回它的 id（**幂等：不重复烧钱** ✅）。

    对应路线图 §7.4："重复提交 → 返回已有 task_id（幂等）"
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun

    with get_session() as s:
        row = s.scalars(
            select(AgentRun).where(
                AgentRun.cluster_id == cluster_id,
                AgentRun.status.in_(("RUNNING", "NEEDS_HUMAN")),
            ).limit(1)
        ).first()
        return str(row.id) if row else None


# ════════════════════════════════════════════════════ Critic 的处置
def apply_critic_verdict(verdict: str, reject_count: int,
                         limits: AgentLimits = DEFAULT_LIMITS) -> str:
    """Critic 判 WRONG 之后：**要不要转人工**？

    路线图 §7.3 的 HITL 停点之一：
        "Critic 连续两次不通过 → 转人工 —— **不让人工被 LLM 卡死**" ✅

    :return: "continue"（还能再试一次）/ "escalate"（转人工）/ "pass"（通过/不判）
    """
    if verdict in ("OK", "SKIP", ""):
        return "pass"
    # WEAK 不算"不通过"（它只是"可疑"），只有 WRONG 计数 ✅
    if verdict != "WRONG":
        return "pass"
    if reject_count >= limits.critic_max_reject:
        return "escalate"
    return "continue"


# ════════════════════════════════════════════════════ 反思（Plan-and-Execute 的 reflect）
def should_reflect(result, limits: AgentLimits = DEFAULT_LIMITS) -> bool:
    """要不要**再补一步证据**再判一次（这就是 reflect 环 ✅）。

    触发条件（三条都要满足）：
        ① 状态是 NEEDS_HUMAN（说明"依据不足"而不是"明确该做"）
        ② **取证步数还没用完**（`max_steps`）
        ③ **还有能补的证据类型没查过**（product / outcome）
    **为什么不无条件 reflect**：
        7B 实测会用尽步数（6/6 走满 5 步）✅
        所以 reflect 必须是**有条件的**、**有界的**，否则就是无限烧钱 ✅
    """
    if getattr(result, "status", "") != "NEEDS_HUMAN":
        return False
    # ⚠️ 只数"取证步"（query_*），不数 llm.judge ✅
    #    第一版数的是 `len(result.steps)` —— 包含了 `llm.judge` ✅ ✗
    #    → 标准流程是 3 取证 + 1 judge = 4 步 ✅
    #    → 而 max_steps=3 → `4 >= 3` → **永远返回 False** ✅ ✗
    #    → 结果：reflect **从来没触发过**（但测试显示"默认 4 步"通过 ✅ 假绿）
    #    "≤3 步"指的是**取证步数**，不是"总步数包含一次判断" ✅
    steps_used = {s.tool for s in getattr(result, "steps", [])
                  if str(s.tool).startswith("query_")}
    if len(steps_used) >= limits.max_steps:
        return False
    # ⚠️ **能补的只有 query_product**（2026-10-31 修正）：
    #    `query_outcome` 要 `proposal_id`，而 agent 手上没有提案 ✅
    #    → 第一版把两个都算成"可补项"，但其中一个根本调不通 ✅ ✗
    #    现在只认 query_product（要 `spu_id`，从 `query_cluster` 的 `spu_ids` 取 ✅）
    return "query_product" not in steps_used
