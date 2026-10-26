# -*- coding: utf-8 -*-
"""
复盘契约（retro.py）
===================
对应 M9 效果复盘（开发指南 09）：回答立项书痛点"改了有没有用"。

核心：动作执行后进入观察窗口，用对照组 DID 量化净效果。
    - OutcomeEffect  单个指标的效果量（DID 算出的"净变化"）
    - ActionOutcome  一个动作的完整复盘结果
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OutcomeEffect(BaseModel):
    """单个指标的效果量（DID 算出的"净变化"，已剔除大盘波动）。"""

    metric: Literal["neg_rate", "refund_rate", "conversion", "gmv", "rating_avg"]
    # 指标名：差评率 / 退款率 / 转化率 / 成交额 / 评分均值

    treat_delta: float
    # 处理组的变化 = 动作后 - 动作前（比如差评率 -6%）

    ctrl_delta: float
    # 对照组的变化 = 同期对照组的变化（比如大盘差评率也降了 -1%）

    effect: float
    # DID 净效果 = treat_delta - ctrl_delta（-6% - (-1%) = -5%，这才是动作真实贡献）

    ci_low: float
    # 置信区间下界（bootstrap 算出）

    ci_high: float
    # 置信区间上界

    significant: bool
    # 是否显著：置信区间不含 0 才算显著（含 0 说明"可能没效果"）


class ActionOutcome(BaseModel):
    """一个动作的完整复盘结果。"""

    outcome_id: str
    # 复盘唯一 ID

    proposal_id: str
    # 对应哪个动作提案（和 action_proposal 关联）

    treat_spu_id: str
    # 处理组：被动作影响的那个商品

    control_spu_ids: list[str] = Field(default_factory=list)
    # 对照组商品列表（同类目、价格带相近、无动作的商品）

    window_days: int
    # 观察窗口天数（L1 动作 7 天 / L2 14 天 / L3 30 天）

    status: Literal["WAITING", "OBSERVING", "READY", "CONCLUDED", "INCONCLUSIVE"] = "WAITING"
    # 状态机：等待 → 观察中 → 到期可算 → 已出结论 / 无法判定

    effects: list[OutcomeEffect] = Field(default_factory=list)
    # 各指标的效果列表

    conclusion: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "INCONCLUSIVE"] | None = None
    # 结论：有效 / 无效 / 无明显变化 / 算不清

    reason_code: str | None = None
    # 当 conclusion = INCONCLUSIVE 时给出原因：
    #   CONTROL_INSUFFICIENT（对照组不足）/ PROMO_INTERFERENCE（撞大促）/
    #   MULTI_ACTION（多次动作）/ DATA_GAP（数据缺失）

    confidence: float | None = Field(default=None, ge=0, le=1)
    # 置信度（同商品多次动作时降级）

    @model_validator(mode="after")
    def check_conclusion_consistency(self):
        """结论自洽性校验（纯逻辑检查，不依赖外部数据，所以不需要 context）。

        两条铁律（对应设计"宁可 INCONCLUSIVE，不给假结论"）：
            1. 结论 POSITIVE 必须至少一个指标显著（否则"有效"从何而来？）
            2. 结论 INCONCLUSIVE 必须给 reason_code（"算不清"要说清为什么）
        """
        if self.conclusion == "POSITIVE" and not any(e.significant for e in self.effects):
            raise ValueError("POSITIVE_WITHOUT_SIGNIFICANT_EFFECT")
        if self.conclusion == "INCONCLUSIVE" and not self.reason_code:
            raise ValueError("INCONCLUSIVE_REQUIRES_REASON")
        return self
