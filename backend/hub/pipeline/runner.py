# -*- coding: utf-8 -*-
"""
流水线运行器（runner.py）
========================
按顺序跑完 8 个步骤，每步：
    1. 打一个 span（记录耗时、状态）
    2. 调用该步的函数
    3. 把产出存进 state.outputs

同时负责更新 analysis_run 的状态（RUNNING → DONE/FAILED）。
"""
from hub.core.tracing import span
from hub.persistence.repository import RunRepo
from hub.pipeline.state import PipelineState
from hub.pipeline.steps import dqe_step
from hub.pipeline.stubs import (
    stub_analysis,
    stub_execution,
    stub_gateway,
    stub_graph,
    stub_ingest,
    stub_multimodal,
    stub_retro,
)

# 流水线的步骤顺序（顺序就是数据流方向，不能乱）
# 每个元素：(步骤名, 该步的函数)
# 已实现的步骤用 steps.py 里的真实函数，未实现的先用 stubs.py 的桩
STEPS = [
    ("ingest", stub_ingest),          # 1. 接入（桩，待 03 接入流水线）
    ("dqe", dqe_step),                # 2. 降噪（真实，04 已实现）
    ("multimodal", stub_multimodal),  # 3. 多模态（桩）
    ("graph", stub_graph),            # 4. 归因（桩）
    ("analysis", stub_analysis),      # 5. 复合分析（桩）
    ("gateway", stub_gateway),        # 6. 工具网关（桩）
    ("execution", stub_execution),    # 7. 执行（桩）
    ("retro", stub_retro),            # 8. 复盘（桩）
]


def run_skeleton(trace_id: str, batch_id, tenant_id: str = "default") -> PipelineState:
    """跑一次 walking skeleton（假数据全链）。

    :param trace_id:  运行 ID
    :param batch_id:  批次 UUID（外键，必须先有 ingest_batch）
    :param tenant_id: 租户
    :return: 跑完的 PipelineState（含各步产出）
    """
    repo = RunRepo()

    # 1. 登记运行，状态置为 RUNNING
    repo.create(trace_id, batch_id, tenant_id)
    repo.update_status(trace_id, "RUNNING")

    state = PipelineState(trace_id=trace_id, batch_id=batch_id, tenant_id=tenant_id)

    try:
        # 2. 按顺序执行每一步（每步一个 span）
        for name, fn in STEPS:
            with span(name, trace_id, impl="stub"):
                state.outputs[name] = fn(state)
    except Exception:
        # 任何一步失败：运行状态置 FAILED，并把异常继续抛出去
        repo.update_status(trace_id, "FAILED")
        raise

    # 3. 全部完成：状态置 DONE，记录统计
    #
    # ⚠️ `module` 必填（阶段 2.6）：
    #   原来这里写的是 `{"steps": len(STEPS)}`，**没有 module** ✅
    #   → `health.py::_latest_stats()` 按 `stats["module"]` 过滤，
    #     所以"跑一次全链"的统计**从来没被 hub health 读到过** ✅ ✗
    #   → 现在补上；`update_status` 也会在缺 module 时直接报错，防止再犯。
    from hub.contracts.runstats import RunStats

    repo.update_status(
        trace_id, "DONE",
        stats=RunStats(module="PIPELINE", counts={"steps": len(STEPS)}).to_jsonb(),
    )
    return state
