# -*- coding: utf-8 -*-
"""
统一报错规范（errors.py）
========================
作用：全项目只用一种"出错方式"，出错信息格式统一、错误编号集中管理。

为什么不能直接 raise Exception("出错了")：
    1. 各写各的，错误信息乱，别人（和几个月后的自己）看不懂；
    2. 无法给错误"编号"，API 返回时无法结构化（{code, message}）；
    3. 无法标记"这个错能不能自动重试"。

怎么用：
    from hub.core.errors import HubError, ErrorCode
    raise HubError(ErrorCode.HEADER_NOT_FOUND, "前 10 行没找到表头")

这样调用方就能：
    except HubError as e:
        print(e.code)        # CPP-1001
        print(e.retryable)   # False
        e.to_dict()          # {"code": "CPP-1001", "message": "..."}
"""
from typing import Any


class HubError(Exception):
    """全项目唯一的业务异常类。所有"预期内的错误"都用它抛。"""

    def __init__(self, code: str, message: str, retryable: bool = False,
                 details: Any = None):
        """
        :param code:      错误编号，如 "CPP-1001"（见下方 ErrorCode 表）
        :param message:   人话解释，如 "前 10 行没找到表头"
        :param retryable: 这个错能不能自动重试（True=可以，如网络超时；False=不行，如参数错误）
        :param details:   可选的补充信息（如"第 5 行"这种具体定位），可以留空
        """
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
        # 调用父类 Exception 的初始化，把错误拼成 "CPP-1001: 前 10 行没找到表头" 这种可读形式
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict:
        """转成字典，方便 API 层直接返回成 JSON 给前端/调用方。"""
        d = {"code": self.code, "message": self.message}
        if self.details is not None:
            d["details"] = self.details
        return d


# ==================== 错误编号表（对应设计文档 §5.3）====================
# 规则：按模块分段，1xxx=接入、2xxx=归一、3xxx=算子、4xxx=检测、5xxx=网关、6xxx=评测
class ErrorCode:
    """错误编号集中在这里，业务代码不要硬编码字符串，用 ErrorCode.XXX。"""

    # 1xxx 数据接入
    HEADER_NOT_FOUND = "CPP-1001"          # 表头没探测到
    ENCODING_UNSUPPORTED = "CPP-1002"      # 文件编码不支持
    DUPLICATE_BATCH = "CPP-1003"           # 同一文件重复导入

    # 2xxx 数据归一
    REQUIRED_FIELD_MISSING = "CPP-2001"    # 必填字段缺失
    RATING_CONFLICT = "CPP-2002"           # 评分冲突（好中差 与 星级 不一致）
    GAP_DETECTED = "CPP-2003"              # 日期缺口

    # 3xxx 算子（抽取/分类/校验）
    SCHEMA_INVALID = "CPP-3001"            # 结构化输出不符合 schema
    EVIDENCE_NOT_GROUNDED = "CPP-3002"     # 模型引用的原文证据不存在（反幻觉）
    BUDGET_EXCEEDED = "CPP-3003"           # token 预算超限

    # 4xxx 检测
    INSUFFICIENT_HISTORY = "CPP-4001"      # 历史数据不足，无法预测
    PREDICTOR_UNAVAILABLE = "CPP-4002"     # 预测器不可用

    # 5xxx 工具网关
    TOOL_NOT_FOUND = "CPP-5001"            # 工具未注册
    SCOPE_DENIED = "CPP-5002"              # 权限不足
    EVIDENCE_MISSING = "CPP-5003"          # 缺少调用依据
    RATE_LIMITED = "CPP-5004"              # 触发限流
    RISK_L3_BLOCKED = "CPP-5006"           # L3 高风险被硬拦截
    PARAM_INVALID = "CPP-5007"             # 参数不合法

    # 6xxx 评测
    HARD_GATE_FAILED = "CPP-6001"          # 硬门禁不通过
    COST_BUDGET_EXCEEDED = "CPP-6002"      # 成本预算超限
