# -*- coding: utf-8 -*-
"""
质量契约（quality.py）
=====================
定义"数据质量判定"相关的结构，对应 DQE 数据降噪（开发指南 04）和校验（P4）。

    - DQEVerdict   DQE 对一条评价的最终判定：保留 / 待复核 / 丢弃
    - Violation    一条校验违规
    - Validation   一条记录的校验结果
"""
from typing import Literal

from pydantic import BaseModel, Field


class DQEVerdict(BaseModel):
    """DQE（数据降噪引擎）对一条评价的最终判定。

    这是 DQE 模块的核心输出，三种决策语义（务必分清）：
        keep   正常数据，进入分析
        review 可疑但有价值（如可疑好评、被降级的差评），仍进分析但打标待复核
        drop   噪音，不进分析（但仍落库，可回溯）
    """
    review_id: str                                   # 对应哪条评价
    decision: Literal["keep", "review", "drop"]      # 三种决策之一
    reason_code: str                                 # 判定依据（如 "R007" / "ML_SPAM"）
    spam_score: float = Field(ge=0, le=1)            # 水军分数 0~1（越高越像水军）
    anomaly_score: float = Field(ge=0, le=1)         # 评分异常分数 0~1
    hit_rules: list[str] = Field(default_factory=list)  # 命中的规则 ID 列表（可解释性）
    engine: Literal["ml+rule", "rule_only"]          # 用哪套引擎判的（可追溯，降级时=rule_only）
    model_version: str | None = None                 # ML 模型版本（纯规则时为空）


class Violation(BaseModel):
    """一条校验违规（P4 校验环节产出）。"""
    rule_id: str                          # 违规的规则编号，如 "R-PRICE-01"
    field: str                            # 出问题的字段，如 "价格"
    severity: Literal["error", "warn"]    # 级别：error=阻断，warn=保留但打标
    detail: str                           # 人话说明


class Validation(BaseModel):
    """一条记录的校验结果（P4 校验环节产出）。"""
    record_id: str                        # 对应哪条记录
    passed: bool                          # 是否通过（True=无 error 级违规）
    violations: list[Violation] = Field(default_factory=list)   # 违规列表（可为空）
