# -*- coding: utf-8 -*-
"""
桩函数（stubs.py）
=================
walking skeleton 用的 8 个"假实现"。

每个桩**只返回一个假的结果字典**，不做任何真实计算。
但它们的**函数签名和真实实现一致**——这样等真实模块写完，
只要把 stubs.py 里的函数换成真实函数，runner 一行都不用改。

对应关系：
    ingest     → hub/ingress/        （真实：解析 xlsx → RawBatch → Record）
    dqe        → hub/dqe/            （真实：规则+ML 降噪 → DQEVerdict）
    multimodal → hub/multimodal/     （真实：Vision 打标 → VisionTags）
    graph      → hub/graph/          （真实：LangGraph 归因 → Attribution）
    analysis   → hub/analysis/       （真实：聚类/背离/优先级）
    gateway    → hub/gateway/        （真实：生成动作提案 + 四道防线）
    execution  → hub/execution/      （真实：执行 + 回查）
    retro      → hub/retro/          （真实：DID 复盘）
"""
from hub.pipeline.state import PipelineState


def stub_ingest(state: PipelineState) -> dict:
    """桩：数据接入。真实实现：读 xlsx、探测表头、字段映射、PII 丢弃。"""
    return {"records": 36, "batch_id": str(state.batch_id)}


def stub_dqe(state: PipelineState) -> dict:
    """桩：数据降噪。真实实现：规则轨 + ML 轨，产出 keep/review/drop。"""
    return {"kept": 30, "review": 3, "dropped": 3}


def stub_multimodal(state: PipelineState) -> dict:
    """桩：多模态沙箱。真实实现：图片阅后即焚，产出 VisionTags。"""
    return {"images_tagged": 12}


def stub_graph(state: PipelineState) -> dict:
    """桩：归因链。真实实现：LangGraph 多步归因，证据必须落地原文。"""
    return {"attributions": 8}


def stub_analysis(state: PipelineState) -> dict:
    """桩：复合分析。真实实现：问题簇聚类、价格背离、优先级、销量预测、CaseRAG。"""
    return {"clusters": 3, "anomalies": 2}


def stub_gateway(state: PipelineState) -> dict:
    """桩：工具网关。真实实现：生成动作提案，过四道防线。"""
    return {"proposals": 3}


def stub_execution(state: PipelineState) -> dict:
    """桩：有界执行链。真实实现：执行低风险动作、高风险转人工、执行后回查。"""
    return {"executed": 2, "hitl": 1}


def stub_retro(state: PipelineState) -> dict:
    """桩：效果复盘。真实实现：对照组 DID，算出净效果。"""
    return {"outcomes": 2}
