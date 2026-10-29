# -*- coding: utf-8 -*-
"""
工具规格与注册表（spec.py）
==========================
M5 网关管的是"能不能做"，而"能做什么"由这张注册表决定。

**默认拒绝**：没注册的工具一律拒绝（`deny_by_default`）。这样 LLM 即使
胡说一个工具名，也只会得到 `TOOL_NOT_FOUND`，不会真的执行什么。

一个工具要声明的东西（每一项都对应网关的一道检查）：
    params_model    参数模型 → 网关做**强类型校验**（不是 if 判断字段在不在）
    risk_level      L0 只读 / L1 无对外 / L2 有限影响 / L3 不可逆或涉及资金
    required_role   L2 要 operator、L3 要 manager（角色跟着工具走，不写死在网关里）
    evidence_types  必须提供哪类依据（有依据才能做，防止"拍脑袋执行"）
    compensate      补偿动作（None = 不可逆，必须人工）
    outcome_metrics 这个动作该看什么效果指标（M9 复盘要用，现在先存着）
    handler         真正干活
    verify          回查：重新读真实状态，**不信 handler 的返回值**

为什么 handler 和 verify 分开：
    handler 可能"以为自己成功了"（写库失败、被回滚、写错表）。
    verify 是**独立地重新查一遍**，两者都通过才算成功——这就是反幻觉第三道防线。
"""
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


# ==================== 风险等级 ====================

class RiskLevel:
    """四级风险。数字越大越危险。"""
    L0 = "L0"   # 只读（查价、查库存）
    L1 = "L1"   # 无对外影响（写内部草稿、写备注）
    L2 = "L2"   # 影响面有限（改详情页、派工单）—— 需审批，有补偿
    L3 = "L3"   # 不可逆 / 对外 / 涉及资金（改价、发布回复）—— 必须人工，无补偿

    ORDER = {L0: 0, L1: 1, L2: 2, L3: 3}
    ALL = (L0, L1, L2, L3)

    @classmethod
    def rank(cls, level: str) -> int:
        return cls.ORDER.get(level, 99)

    @classmethod
    def needs_approval(cls, level: str) -> bool:
        """L2 及以上需要人点头。"""
        return cls.rank(level) >= cls.rank(cls.L2)

    @classmethod
    def is_auto(cls, level: str) -> bool:
        """L0/L1 可以系统自己干。"""
        return cls.rank(level) <= cls.rank(cls.L1)


# ==================== 调用上下文 ====================

@dataclass
class CallContext:
    """一次工具调用的上下文（谁、在哪个租户、属于哪次运行）。"""
    tenant_id: str = "default"
    role: str = "system"       # system / operator / manager
    trace_id: str = ""
    run_id: str = ""
    proposal_id: str = ""

    def has_role(self, required: str | None) -> bool:
        """检查角色。required=None 表示不限制（L0/L1 就是这样）。

        ⚠️ 这里**没有"system 豁免"**：
            L0/L1 走自动执行时根本不查角色（required_role=None），
            所以 system 角色不需要任何特权。
            如果给 system 开豁免，就等于"系统自己生成的 L3 提案可以跳过人工批准"，
            正好破坏"改价必须有人点头"这条铁律。
        """
        if not required:
            return True
        order = {"system": 0, "operator": 1, "manager": 2}
        return order.get(self.role, -1) >= order.get(required, 99)


# ==================== 回查结果 ====================

@dataclass
class VerifyResult:
    """回查结果。"""
    ok: bool
    detail: dict


# ==================== 工具规格 ====================

@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。"""
    name: str                          # 全局唯一，如 product.update_price
    version: str                       # 语义化版本（幂等键要用）
    description: str                   # 给 LLM 看的说明
    params_model: type[BaseModel]      # 参数 schema
    risk_level: str                    # L0~L3
    handler: Callable[[dict, CallContext], dict]
    verify: Callable[[dict, CallContext, dict], "VerifyResult"]

    required_role: str | None = None           # 需要什么角色
    evidence_types: frozenset[str] = frozenset()   # 需要哪类依据
    compensate: str | None = None              # 补偿动作名（None=不可逆）
    outcome_metrics: tuple[str, ...] = ()      # M9 复盘看什么指标
    idempotent: bool = True
    timeout_s: float = 5.0


# 签名说明：
#   handler(args, ctx) -> dict           真正干活，返回结果
#   verify(args, ctx, result) -> VerifyResult
#        result 是 handler 的返回值，只用来知道"该回查哪一行/哪个 id"，
#        **不当作成功证据**——verify 必须自己去库里重新读一遍。


# ==================== 注册表 ====================

_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    """注册一个工具。重名直接报错——静默覆盖会让"哪个版本在跑"变得不可知。"""
    if spec.name in _REGISTRY:
        raise ValueError(f"工具重复注册：{spec.name}")
    if spec.risk_level not in RiskLevel.ALL:
        raise ValueError(f"非法风险等级：{spec.risk_level}")
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> ToolSpec | None:
    """按名字取工具；没注册返回 None（网关据此拒绝）。"""
    return _REGISTRY.get(name)


def all_specs() -> list[ToolSpec]:
    """所有已注册工具（给 LLM 选工具时用）。"""
    return sorted(_REGISTRY.values(), key=lambda s: s.name)


def find_by_risk(level: str) -> list[ToolSpec]:
    """按风险等级筛（演示/测试用）。"""
    return [s for s in all_specs() if s.risk_level == level]


def clear() -> None:
    """清空注册表（测试用）。"""
    _REGISTRY.clear()


def describe_all() -> list[dict[str, Any]]:
    """给 LLM / 前端看的工具清单（不含 handler）。"""
    return [
        {
            "name": s.name,
            "version": s.version,
            "description": s.description,
            "risk_level": s.risk_level,
            "required_role": s.required_role,
            "evidence_types": sorted(s.evidence_types),
            "compensable": s.compensate is not None,
            "params": list(s.params_model.model_fields.keys()),
        }
        for s in all_specs()
    ]
