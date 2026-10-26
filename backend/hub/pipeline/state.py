# -*- coding: utf-8 -*-
"""
流水线状态（state.py）
=====================
PipelineState 在 8 个步骤之间传递：前面的步骤产出放进 outputs，后面的步骤读取。

为什么用"一个状态对象"而不是"一步步传参"：
    步骤多了（8 步），参数会爆炸。统一放进 state，每步只取自己需要的。
"""
from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class PipelineState:
    """一次流水线运行的状态。"""

    trace_id: str
    # 本次运行的 ID（同时是 trace_id，贯穿全链）

    batch_id: UUID
    # 数据批次 ID（本次分析的是哪批导入数据）

    tenant_id: str = "default"
    # 租户

    outputs: dict[str, dict] = field(default_factory=dict)
    # 各步骤的产出，key=步骤名（如 "dqe"），value=该步返回的字典
