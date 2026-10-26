# -*- coding: utf-8 -*-
"""
网关契约（gateway.py）
=====================
对应 M5 工具网关 + M6 有界执行链（开发指南 08）。

核心思想："把脚本变成工具，把工具变成系统"。
    - ToolSpec  一个工具的"档案"（能力 + 治理规则一起声明）
    - ToolCall  LLM 提出的一次"我想调这个工具"（含依据）
    - ToolResult 执行结果
    - VerifyResult 执行后的状态回查（反幻觉：不信工具自述，独立查真实状态）
"""
from enum import IntEnum

from pydantic import BaseModel, Field


class RiskLevel(IntEnum):
    """风险等级，数值越大越危险（可直接用 > < 比较）。"""
    L0 = 0   # 只读：查库存、查工单状态
    L1 = 1   # 可逆、无对外影响：生成草稿、写内部备注
    L2 = 2   # 影响有限、需留痕：派发工单、改内部标签
    L3 = 3   # 不可逆/对外/资金：改价、下架、批量回复（网关硬拦截转人工）

    @property
    def label(self) -> str:
        """返回 "L0"~"L3" 字符串，用于落库和日志。"""
        return f"L{self.value}"


class RateLimit(BaseModel):
    """限流配置。"""
    qps: float = 5.0     # 每秒允许的调用次数
    burst: int = 10      # 突发峰值（瞬时最多几次）


class ToolSpec(BaseModel):
    """一个工具的完整声明，注册进网关的"档案"。

    注意：能力和治理写在一起——
        params_schema 描述"能干什么"
        risk_level / scopes / evidence_types / compensate 描述"能不能干、怎么监督"
    """
    name: str                            # 全局唯一工具名，如 crm.ticket.create
    version: str = "v1"                  # 版本（用于幂等键和回放）
    description: str = ""                # 用途说明（供 LLM 选择工具时看）
    params_schema: dict = Field(default_factory=dict)   # 参数的 JSON Schema
    risk_level: RiskLevel = RiskLevel.L1               # 风险等级
    scopes: list[str] = Field(default_factory=list)    # 所需权限（如 ["crm:write"]）
    idempotent: bool = True              # 是否幂等（可安全重试）
    timeout_s: float = 10.0              # 超时（秒）
    rate_limit: RateLimit = Field(default_factory=RateLimit)   # 限流
    evidence_types: list[str] = Field(default_factory=list)    # 必须提供的依据类型
    compensate: str | None = None        # 补偿动作名；None = 不可补偿 → 必须人工（强制 L3）


class ToolCall(BaseModel):
    """一次工具调用请求（LLM 提出的"我想调这个工具"）。"""
    tool: str                            # 要调的工具名
    args: dict = Field(default_factory=dict)   # 参数
    evidence_refs: list[str] = Field(default_factory=list)   # 依据引用（防"突发奇想乱调"）
    idempotency_key: str | None = None   # 幂等键（同一请求重复提交，只执行一次）


class ToolResult(BaseModel):
    """工具执行结果。"""
    ok: bool                             # 是否成功
    external_id: str | None = None       # 外部系统的回执 ID（如工单号），供回查
    data: dict = Field(default_factory=dict)   # 返回数据
    error_code: str | None = None        # 失败时的错误码


class VerifyResult(BaseModel):
    """执行后的"状态回查"结果。

    反幻觉关键：不能信工具自己说"成功"，要独立去查真实状态。
    """
    ok: bool                             # 真实状态是否与预期一致
    reason: str | None = None            # 不一致的原因（如 "NOT_FOUND" / "STATE_MISMATCH"）
    snapshot: dict | None = None         # 回查到的真实状态快照
