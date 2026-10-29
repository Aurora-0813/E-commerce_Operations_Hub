# -*- coding: utf-8 -*-
"""
网关的数据结构（schemas.py）
===========================
一次工具调用的输入（ToolCall）和输出（GatewayResult）。

**拒绝原因用枚举，不用字符串**：
    错误码是给代码判断的，人话是给运营看的，两者分开。
    `DenyCode.EVIDENCE_MISMATCH` 和 "证据指向的商品和要改的商品不一致" 是同一件事的两种表述。
"""
from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    """网关的判定结果（五种，没有第六种）。"""
    EXECUTED = "EXECUTED"                  # L0/L1：执行了，而且回查通过
    REPLAYED = "REPLAYED"                  # 幂等命中：没重复执行，返回上次结果
    PENDING_APPROVAL = "PENDING_APPROVAL"  # L2：等人工审批（没执行）
    PENDING_HITL = "PENDING_HITL"          # L3：转人工处理（没执行）
    REJECTED = "REJECTED"                  # 被拒（参数/权限/依据/频控）


class DenyCode(str, Enum):
    """拒绝原因。默认拒绝是最后一道保险。"""
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"        # 工具没注册
    PARAM_INVALID = "PARAM_INVALID"          # 参数类型/边界/多余字段
    SCOPE_DENIED = "SCOPE_DENIED"            # 角色不够
    EVIDENCE_MISSING = "EVIDENCE_MISSING"    # 没提供必需类别的依据
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"   # 依据 ID 在库里不存在
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"  # 依据指向的东西和动作对不上
    RATE_LIMITED = "RATE_LIMITED"            # 超频
    HANDLER_ERROR = "HANDLER_ERROR"          # 工具执行抛异常
    VERIFY_FAILED = "VERIFY_FAILED"          # 执行了但回查不一致


@dataclass
class ToolCall:
    """一个"想做某件事"的请求。

    :param tool_name:     工具名（必须在注册表里）
    :param args:          参数（会被 params_model 强校验）
    :param evidence_refs: 依据引用，如 {"issue_cluster": "<uuid>"} 或 {"anomaly": 123}
    :param proposal_id:   来自哪个提案（审计用，可空）
    """
    tool_name: str
    args: dict
    evidence_refs: dict = field(default_factory=dict)
    proposal_id: str = ""


@dataclass
class GatewayResult:
    """网关判定结果。"""
    decision: Decision
    tool_name: str
    execution_id: str | None = None       # tool_execution.id（审计）
    response: dict | None = None          # 工具的返回（REPLAYED 时是上次的）
    deny_code: str | None = None
    detail: dict = field(default_factory=dict)
    verify_ok: bool | None = None

    @property
    def ok(self) -> bool:
        return self.decision in (Decision.EXECUTED, Decision.REPLAYED)

    def brief(self) -> str:
        """给 CLI 打印的一行摘要。"""
        if self.decision == Decision.EXECUTED:
            v = "回查✅" if self.verify_ok else "回查❌"
            return f"{self.tool_name} → 已执行（{v}）"
        if self.decision == Decision.REPLAYED:
            return f"{self.tool_name} → 幂等命中，未重复执行"
        if self.deny_code:
            return f"{self.tool_name} → 拒绝（{self.deny_code}）"
        return f"{self.tool_name} → {self.decision.value}"
