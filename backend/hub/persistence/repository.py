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

    def create(self, trace_id: str, batch_id=None, tenant_id: str = "default") -> None:
        """新建一次运行（状态默认 QUEUED）。

        :param trace_id:  贯穿全链路的 ID（业务主键）
        :param batch_id:  所属批次 UUID（**可空**：跨批次的运行，如 M3 同时分析淘宝+京东）
        :param tenant_id: 租户
        """
        with get_session() as s:
            s.add(AnalysisRun(trace_id=trace_id, batch_id=batch_id, tenant_id=tenant_id))
            s.commit()

    def get(self, trace_id: str) -> AnalysisRun | None:
        """按 trace_id 查一次运行，查不到返回 None。"""
        with get_session() as s:
            return s.scalar(select(AnalysisRun).where(AnalysisRun.trace_id == trace_id))

    def update_status(self, trace_id: str, status: str,
                      stats: dict | None = None) -> None:
        """更新运行状态。

        :param status: QUEUED / RUNNING / DONE / DEGRADED / FAILED
        :param stats:  统计信息（可空）；传入就覆盖
        """
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
