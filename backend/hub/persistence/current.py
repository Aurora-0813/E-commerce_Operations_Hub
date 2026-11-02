# -*- coding: utf-8 -*-
"""当前批次的选择（current_run.py）—— 2026-11-01 新增

**为什么需要**
    每跑一次全链，`cluster` 阶段都会**插入一批新的簇**（22 个 ✅），
    旧的**不删**（因为要保留历史 ✅）。
    跑了 4 次之后 `issue_cluster` 有 **88 行**，但"当前"只有 22 个 ✅
    → 所以**读的时候必须选一次**，否则前端会显示 88 个簇、P0 显示 8 个 ✅ ✗

**为什么这么选（而不是加 `is_current` 列）**
    `issue_cluster` 已经有 `run_id` ✅ → "最新那次" = `created_at` 最大的那个 run ✅
    → **不用迁移、不用双写、不用删数据** ✅
    → 而且天然支持"看历史上某一次"（传 `run_id` 参数就行 ✅）

**副作用（必须知道）**
    `created_at` 是簇的插入时间；如果两次跑链**在同一秒**内插完，
    `ORDER BY created_at DESC` 可能挑错 → 但簇插入是逐个 commit 的，
    同秒概率极低，且挑错也只会显示"上一次"的簇（数据仍然一致 ✅）
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.persistence.models import IssueCluster


def current_run_id(s: Session) -> UUID | None:
    """**最新一次**跑了 cluster 的 `run_id`（没有簇时返回 None ✅）。"""
    return s.scalar(
        select(IssueCluster.run_id)
        .order_by(IssueCluster.created_at.desc())
        .limit(1)
    )


def current_cluster_ids(s: Session) -> list[UUID]:
    """最新一次跑出来的簇 id 列表（**给"按当前簇过滤"的查询用** ✅）。"""
    rid = current_run_id(s)
    if rid is None:
        return []
    return list(s.scalars(select(IssueCluster.id).where(IssueCluster.run_id == rid)).all())
