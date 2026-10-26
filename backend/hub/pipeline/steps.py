# -*- coding: utf-8 -*-
"""
真实步骤（steps.py）
===================
pipeline 里**已实现**的步骤（区别于 stubs.py 里的桩函数）。

约定：每个步骤接收 PipelineState，返回一个结果字典。
写好后，把 runner.py 的 STEPS 里对应的 stub 换成这里的函数即可。

当前真实步骤：
    dqe_step  —— 数据降噪（04 已实现）
其余模块写好后逐个替换。
"""
from hub.pipeline.state import PipelineState


def dqe_step(state: PipelineState) -> dict:
    """真实 DQE：对当前批次跑数据降噪。

    注意：run_dqe 按 batch_id 从 record 表读数据、判定后回填，
    所以它不依赖 state.outputs 里上一步的产出——这是"落库解耦"的好处。
    """
    from hub.dqe.engine import run_dqe      # 延迟导入，避免循环依赖

    stats = run_dqe(batch_id=state.batch_id)
    return {
        "processed": stats.total,
        "keep": stats.keep,
        "review": stats.review,
        "drop": stats.drop,
        "engine": stats.engine,
    }
