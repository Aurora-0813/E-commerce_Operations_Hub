# -*- coding: utf-8 -*-
"""
契约层（contracts）
==================
这里是模块之间通信的**数据结构定义**：跨模块传递的数据长什么样，只在这里定义一次。

规则（铁律）：
    1. 跨模块的数据结构只在这里定义一次，不要在各模块里再写一份；
    2. 这里绝不 import 任何业务模块（保持"契约"纯净、无依赖）；
    3. 加字段先改这里，再改实现。

本文件是"包入口"，把所有契约 re-export，方便统一导入：
    from hub.contracts import RawBatch, Record, DQEVerdict, Attribution, VisionTags

────────────────────────────────────────────────────────────────
2026-10-31 修正（阶段 0.3 还债）
────────────────────────────────────────────────────────────────
原文件顶部写的是"全项目唯一真相"，但实测有 3/8 个模块**零使用者**，
而且同一批类在别处各有一份实现 —— 那句话是不成立的。已按实际情况对齐：

· **删除** `gateway.py`（80 行，零使用者）
  `ToolSpec` / `RiskLevel` / `VerifyResult` 的真身在 `hub/tools/spec.py`；
  `ToolCall` 的真身在 `hub/gateway/schemas.py`（字段是 `tool_name`，不是 `tool`）；
  `RateLimit` 真身是 `hub/gateway/checks.py` 的 `RateLimiter`（进程内令牌桶）。
  → 链路是"LLM 想调 → 网关判定 → 工具执行"，这条链路上每一层的结构都在各自模块里，
    再在契约层复制一份只会产生两个真相。

· **删除** `retro.py`（88 行，零使用者）
  `ActionOutcome` 的真身是 **`hub/persistence/models.py` 的数据库表** ——
  复盘的"边界结构"就是这张表（`conclusion` / `reason_code` / `effect` 都在列里）。
  `hub/retro/` 内部算 DID 用的是 dataclass（计算结果，不跨模块），不需要另一个模型。

· **保留** `case.py` 但标 `# PLANNED: 阶段 3.5（Case RAG）`
  它定义 `CaseEntry` / `RetrievedCase` / `CaseAugmentedSuggestion.check_citations`
  —— 那是"引用必须落地校验"的契约，阶段 3.5 会真的用上。

**教训**：契约层的价值是"一个定义"，不是"多一层文件"。
写之前先 grep 一遍谁在用；写之后要有测试盯着"它真的被用"。
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
from hub.contracts.runstats import (
    KNOWN_MODULES,
    RunStats,
    check_stats,
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
    # runstats（运行统计）—— 阶段 2.6 新增
    "KNOWN_MODULES",
    "RunStats",
    "check_stats",
    # case（案例检索）—— PLANNED: 阶段 3.5
    "CaseAugmentedSuggestion",
    "CaseEntry",
    "RetrievedCase",
]
