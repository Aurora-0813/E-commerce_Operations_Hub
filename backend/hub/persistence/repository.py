# -*- coding: utf-8 -*-
"""
数据读写（repository.py）
========================
把"怎么读写数据库"封装成方法，业务代码不写 SQL、不碰 Session。

当前只有 RunRepo（分析运行的读写），够骨架用。
注意：运行表叫 analysis_run（模型类 AnalysisRun），用 trace_id 作为业务主键查询。
"""
from datetime import datetime

from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import AnalysisRun


class RunRepo:
    """分析运行（analysis_run）的增查改。"""

    def create(self, trace_id: str, batch_id=None, tenant_id: str = "default"):
        """新建一次运行（状态默认 QUEUED），返回 run 的 UUID。

        :param trace_id:  贯穿全链路的 ID（业务主键）
        :param batch_id:  所属批次 UUID（**可空**：跨批次的运行，如 M3 同时分析淘宝+京东）
        :param tenant_id: 租户
        :return: analysis_run.id（issue_cluster / anomaly 的外键要用它，不是 trace_id）
        """
        with get_session() as s:
            run = AnalysisRun(trace_id=trace_id, batch_id=batch_id, tenant_id=tenant_id)
            s.add(run)
            s.commit()
            s.refresh(run)          # 拿到 DB 生成的 id
            return run.id

    def get(self, trace_id: str) -> AnalysisRun | None:
        """按 trace_id 查一次运行，查不到返回 None。"""
        with get_session() as s:
            return s.scalar(select(AnalysisRun).where(AnalysisRun.trace_id == trace_id))

    def update_status(self, trace_id: str, status: str,
                      stats: dict | None = None) -> None:
        """更新运行状态。

        :param status: QUEUED / RUNNING / DONE / DEGRADED / FAILED
        :param stats:  统计信息（可空）；传入就覆盖。
                       **必须带 `module`** —— 否则 `_latest_stats()` 永远查不到它
                       （阶段 2.6：`pipeline/runner.py` 就因为漏了 module，
                        导致"跑一次全链"的统计从来没被 hub health 读到过）✅
        :raises ValueError: stats 不是 dict，或缺 module
        """
        # 写入前守门：所有 6 个写入点都走这里，所以只要拦一处就够 ✅
        from hub.contracts.runstats import check_stats
        stats = check_stats(stats)

        with get_session() as s:
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.trace_id == trace_id))
            if run is None:
                return
            run.status = status
            if stats is not None:
                run.stats = stats
            # 完成或失败时，记录结束时间
            if status in ("DONE", "FAILED", "DEGRADED"):
                run.finished_at = datetime.now()
            s.commit()
