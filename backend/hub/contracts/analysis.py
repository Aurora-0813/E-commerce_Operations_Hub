# -*- coding: utf-8 -*-
"""
分析契约（analysis.py）
======================
定义"归因分析"相关的结构，对应 M3 归因链（开发指南 06）和 M4 复合分析（07）。

核心设计（反幻觉）：
    Attribution 里有个 model_validator，强制校验"模型引用的证据原文，
    必须是这条评价原文里的真实片段"。这是整个项目最值钱的一行——直接
    杀死"模型编造证据"这类幻觉。
"""
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator


class ReviewTopic(StrEnum):
    """差评主题（封闭枚举，禁止模型自由发挥）。

    为什么用枚举而不是让模型随便写：
        封闭枚举 → 可统计、可比较、可评测。模型只能从这几个里选，
        下游统计"多少条是质量问题、多少条是尺码问题"才成立。
    取值与 data/mock/gold_labels.csv 的"主题分类"列保持一致。
    """
    QUALITY = "quality"               # 质量问题（开线/起球/掉色/缩水）
    SIZE = "size"                     # 尺码问题（偏大/偏小）
    LOGISTICS = "logistics"           # 物流（慢/破损）
    SERVICE = "service"               # 服务（客服态度差）
    DESC_MISMATCH = "desc_mismatch"   # 描述不符（色差/面料不符）
    MISSHIP = "misship"               # 错发漏发
    OTHER = "other"                   # 其他


class Evidence(BaseModel):
    """一条证据：模型说"因为原文里这句"，必须能对上原文。"""
    review_id: str                              # 证据来自哪条评价
    quote: str = Field(min_length=4, max_length=80)   # 引用的原文片段（4~80 字）
    signal: Literal["text", "image", "rating"]  # 证据类型：文本 / 图片 / 评分


class Attribution(BaseModel):
    """一条归因结果：这条差评到底因为什么。"""
    root_cause: ReviewTopic                     # 根因（封闭枚举）
    confidence: float = Field(ge=0, le=1)       # 置信度 0~1
    evidence: list[Evidence] = Field(min_length=1, max_length=5)  # 证据 1~5 条，至少 1 条
    explanation: str = Field(max_length=300)    # 一句人话解释（≤300 字）

    @model_validator(mode="after")
    def ground_evidence(self, info: ValidationInfo):
        """反幻觉校验：证据的 quote 必须是原文真实子串。

        原理：调用方在 model_validate(...) 时，通过 context 传入
        {"content_by_id": {review_id: 原文}}，这里逐条核对：
            - 引用的 review_id 必须存在
            - quote 必须是该原文的子串
        否则抛 ValueError，模型这条输出作废（触发修复重试，见开发指南 06）。
        """
        content_by_id: dict = (info.context or {}).get("content_by_id", {})
        for e in self.evidence:
            raw = content_by_id.get(e.review_id)
            if raw is None or e.quote not in raw:
                raise ValueError(f"EVIDENCE_NOT_GROUNDED:{e.review_id}")
        # 高置信（≥0.8）却只有 1 条证据 → 不可信，也要拒
        if self.confidence >= 0.8 and len(self.evidence) < 2:
            raise ValueError("EVIDENCE_INSUFFICIENT_FOR_HIGH_CONFIDENCE")
        return self


class Classification(BaseModel):
    """一条评价的主题分类结果（P3 分类环节产出）。"""
    record_id: str                              # 对应哪条评价
    topic: ReviewTopic | None = None            # 主题；好评可为 None（没有问题主题）
    severity: int = Field(default=1, ge=1, le=5)   # 严重程度 1~5
    confidence: float = Field(ge=0, le=1)       # 置信度
    impl: str = "classify.v1"                   # 由哪个实现产出


class IssueCluster(BaseModel):
    """一个问题簇：多条评价聚成的"同一类问题"。"""
    cluster_id: str                             # 簇的唯一 ID
    label: str = Field(min_length=2, max_length=20)   # 簇名（人话，如"掉色严重"）
    business_domain: str                        # 业务域（如"女装/连衣裙"）
    platforms: list[str] = Field(default_factory=list)  # 涉及哪些平台（跨平台簇会含两个）
    review_count: int = Field(ge=1)             # 簇里有多少条评价
    growth_rate: float | None = None            # 环比增速（和上一次 run 比，可空）
    priority_score: float | None = None         # 优先级分 0~100（M4 算出）
    representative_review_ids: list[str] = Field(default_factory=list)  # 代表评价（用于展示/检索）


class Anomaly(BaseModel):
    """一个业务异常（价格背离 / 销量波动 / 无效订单）。"""
    anomaly_id: str                             # 异常唯一 ID
    type: Literal["price", "sales", "order"]    # 异常类型
    key: str                                    # 异常主体（如 spu_id / order_id）
    score: float = Field(ge=0, le=100)          # 异常分（可排序）
    reason: str                                 # 人话解释（如"京东价高于同款中位价 38%"）
    evidence: dict = Field(default_factory=dict)   # 证据快照
    impl: str = "detect.v1"                     # 由哪个检测器产出
    confidence: Literal["high", "medium", "low"] = "medium"   # 置信档位


class Priority(BaseModel):
    """一条记录的优先级（P0~P3，P0 最紧急）。"""
    score: float = Field(ge=0, le=100)          # 0~100 分
    tier: Literal["P0", "P1", "P2", "P3"]       # 分档
    factors: dict = Field(default_factory=dict)  # 各因子贡献（reach/severity/trend/actionability）
