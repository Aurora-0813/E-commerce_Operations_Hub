# -*- coding: utf-8 -*-
"""
工具网关（gateway）—— M5
========================
回答一个问题：**这个动作能不能做？**

    invoke(ToolCall, CallContext) → GatewayResult

四道防线（顺序不可交换）：
    ① 工具存在 + 参数强校验 + 角色
    ② 依据（类别 / 存在 / 对得上东西）
    ③ 幂等（两段式：频控前查，频控后占位）
    ④ 频控（令牌桶）

分级（在校验之后，不占用审批资源）：
    L0/L1 → 执行 + 回查 + 审计
    L2    → PENDING_APPROVAL（不执行）
    L3    → PENDING_HITL（不执行，必须人工）
"""
from hub.gateway.checks import LIMITER, RateLimiter
from hub.gateway.invoke import invoke
from hub.gateway.schemas import Decision, DenyCode, GatewayResult, ToolCall

__all__ = [
    "Decision",
    "DenyCode",
    "GatewayResult",
    "ToolCall",
    "RateLimiter",
    "LIMITER",
    "invoke",
]
