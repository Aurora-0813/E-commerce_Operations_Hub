# -*- coding: utf-8 -*-
"""
工具层（tools）
===============
M5 网关的"能做什么"由这里的注册表决定。

    spec.py     ToolSpec / RiskLevel / CallContext / 注册表
    builtin.py  4 个业务工具 + 1 个补偿工具（覆盖 L0~L3）

用法：
    from hub.tools import registry      # 导入即注册
    from hub.tools.spec import get, all_specs
"""
from hub.tools.builtin import register_all

# 导入即注册（幂等）
register_all()

from hub.tools.spec import (            # noqa: E402  (要放在 register_all 之后)
    CallContext,
    RiskLevel,
    ToolSpec,
    VerifyResult,
    all_specs,
    describe_all,
    find_by_risk,
    get,
    register,
)

__all__ = [
    "CallContext",
    "RiskLevel",
    "ToolSpec",
    "VerifyResult",
    "all_specs",
    "describe_all",
    "find_by_risk",
    "get",
    "register",
]
