# -*- coding: utf-8 -*-
"""
pipeline_run 允许 CANCELLED（2026-11-04）
========================================
**为什么**
    用户诉求："开始分析后要有**停止任务**按钮，真的在后端停止" ✅
    实现：`POST /api/runs/{id}/cancel` 把 `pipeline_run.status` 改成 `CANCELLED`
          → worker 在**每个阶段结束时**查一次，看到就退出 ✅

**踩的坑**
    `pipeline_run` 上有个 CHECK 约束 `ck_pipeline_run_status`，
    只允许 `QUEUED/RUNNING/DONE/DEGRADED/FAILED/DEAD` ✅
    → 我第一次改状态直接 **500 CheckViolation** ✅ ✗
    → 这个迁移把它补上 `CANCELLED` ✅

Revision ID: c9e3f4a5b6d7
Revises: b8d2e3f4a5c6
Create Date: 2026-11-04
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9e3f4a5b6d7"
down_revision: str | None = "b8d2e3f4a5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "('QUEUED','RUNNING','DONE','DEGRADED','FAILED','DEAD')"
NEW = "('QUEUED','RUNNING','DONE','DEGRADED','FAILED','DEAD','CANCELLED')"


def upgrade() -> None:
    op.drop_constraint("ck_pipeline_run_status", "pipeline_run", type_="check")
    op.create_check_constraint(
        "ck_pipeline_run_status", "pipeline_run", f"status IN {NEW}",
    )


def downgrade() -> None:
    # 回滚前先把 CANCELLED 的记录改成 DEAD（否则约束加不回去 ✅）
    op.execute("UPDATE pipeline_run SET status='DEAD' WHERE status='CANCELLED'")
    op.drop_constraint("ck_pipeline_run_status", "pipeline_run", type_="check")
    op.create_check_constraint(
        "ck_pipeline_run_status", "pipeline_run", f"status IN {OLD}",
    )
