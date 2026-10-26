# -*- coding: utf-8 -*-
"""
案例契约（case.py）
=================
对应 Case RAG（设计文档 §3.4.5）：检索历史处置案例，给新问题生成建议。

和 retro.py 的关系：复盘结论（POSITIVE/NEGATIVE）沉淀成 CaseEntry，供检索复用。
    - CaseEntry                   一条历史案例（只收录结论明确的）
    - RetrievedCase               检索命中的案例（比 CaseEntry 多检索得分）
    - CaseAugmentedSuggestion     基于历史案例生成的建议
"""
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator

from hub.contracts.analysis import Evidence, ReviewTopic


class CaseEntry(BaseModel):
    """一条历史处置案例。

    只收录结论明确（POSITIVE / NEGATIVE）的案例：
        POSITIVE = "这类问题当时这么做，有效"（该怎么做）
        NEGATIVE = "当时这么做，无效/变差"（别这么做）
    NEUTRAL / INCONCLUSIVE 的复盘不入库（没参考价值）。
    """

    case_id: str
    # 案例唯一 ID

    cluster_label: str = Field(min_length=2, max_length=20)
    # 簇名（人话），如"掉色严重"

    root_cause: ReviewTopic
    # 根因（复用归因的封闭枚举）

    business_domain: str
    # 业务域，如"女装/连衣裙"（检索时的过滤条件）

    platforms: list[str] = Field(default_factory=list)
    # 涉及哪些平台

    action_taken: str
    # 当时做了什么（工具名 + 摘要），如"item.price.update 降价 10%"

    outcome_conclusion: Literal["POSITIVE", "NEGATIVE"]
    # 当时的复盘结论（来自 DID，是 ground truth）

    effect_size: float | None = None
    # 效果量，如差评率变化 -0.042（负号 = 下降 = 变好）

    representative_text: str
    # 代表文本（簇名 + 代表评价 + 根因），用于编码成向量做检索


class RetrievedCase(CaseEntry):
    """检索命中的案例（比 CaseEntry 多了检索得分）。"""

    similarity: float = Field(ge=0, le=1)
    # 余弦相似度（向量检索的原始得分）

    rank_score: float = Field(ge=0, le=1)
    # 重排分 = 0.6 * 相似度 + 0.4 * |effect_size| 归一化
    # 含义：既要"像这个问题"，又要"效果明显"，两者加权排序


class CaseAugmentedSuggestion(BaseModel):
    """基于历史案例生成的建议（注入检索结果后，由 LLM 生成）。"""

    action: str
    # 建议动作，如"建议降价 10% 并优化详情页说明洗涤方式"

    expected_effect: str
    # 预期效果（只能引用案例里的 effect_size，不能自己编百分比）

    cited_cases: list[str] = Field(default_factory=list, max_length=5)
    # 引用的案例 ID（最多 5 个），必须来自检索结果

    confidence: float = Field(ge=0, le=1)
    # 置信度

    evidence: list[Evidence] = Field(default_factory=list)
    # 证据（复用归因的 Evidence，保持"建议也有据可依"的哲学）

    no_history: bool = False
    # 是否"无历史案例"：True 时给保守建议，且禁止引用任何案例

    @model_validator(mode="after")
    def check_citations(self, info: ValidationInfo):
        """引用校验：cited_cases 只能来自检索结果（防"编造案例"）。

        和归因的"证据落地"是同一哲学：归因靠原文证据，建议靠历史案例。
        检索结果通过 context 传入 {"case_ids": {允许引用的 case_id 集合}}。
        """
        allowed: set = (info.context or {}).get("case_ids", set())
        if self.no_history:
            # 说"无历史案例"却还引用案例 → 矛盾
            if self.cited_cases:
                raise ValueError("NO_HISTORY_CONFLICTS_CITATIONS")
        elif not set(self.cited_cases) <= allowed:
            # 引用了检索结果里不存在的案例 → 编造
            raise ValueError("CASE_NOT_GROUNDED")
        return self
