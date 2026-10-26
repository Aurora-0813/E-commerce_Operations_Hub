# -*- coding: utf-8 -*-
"""
契约层（contracts）
==================
这里是全项目所有"数据结构"的定义，是模块之间通信的唯一标准。

规则（铁律）：
    1. 业务代码只能 from hub.contracts import XXX 来用这些结构；
    2. 这里绝不 import 任何业务模块（保持"契约"纯净、无依赖）；
    3. 以后加新字段、新结构，都先改这里，再改实现。

本文件是"包入口"，把所有契约 re-export，方便统一导入：
    from hub.contracts import RawBatch, Record, DQEVerdict, Attribution, VisionTags
"""
from hub.contracts.ingress import (
    BadRow,
    MappingReport,
    RawBatch,
    RawSheet,
    Record,
)
from hub.contracts.quality import (
    DQEVerdict,
    Validation,
    Violation,
)
from hub.contracts.analysis import (
    Anomaly,
    Attribution,
    Classification,
    Evidence,
    IssueCluster,
    Priority,
    ReviewTopic,
)
from hub.contracts.multimodal import (
    VisionTags,
)
from hub.contracts.gateway import (
    RateLimit,
    RiskLevel,
    ToolCall,
    ToolResult,
    ToolSpec,
    VerifyResult,
)
from hub.contracts.retro import (
    ActionOutcome,
    OutcomeEffect,
)
from hub.contracts.case import (
    CaseAugmentedSuggestion,
    CaseEntry,
    RetrievedCase,
)

# 把所有对外可用的契约列在这里，编辑器/IDE 也能自动提示
__all__ = [
    # ingress（数据接入）
    "BadRow",
    "MappingReport",
    "RawBatch",
    "RawSheet",
    "Record",
    # quality（数据质量）
    "DQEVerdict",
    "Validation",
    "Violation",
    # analysis（归因分析）
    "Anomaly",
    "Attribution",
    "Classification",
    "Evidence",
    "IssueCluster",
    "Priority",
    "ReviewTopic",
    # multimodal（多模态）
    "VisionTags",
    # gateway（工具网关）
    "RateLimit",
    "RiskLevel",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "VerifyResult",
    # retro（效果复盘）
    "ActionOutcome",
    "OutcomeEffect",
    # case（案例检索）
    "CaseAugmentedSuggestion",
    "CaseEntry",
    "RetrievedCase",
]
