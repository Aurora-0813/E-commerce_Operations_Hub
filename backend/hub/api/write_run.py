# -*- coding: utf-8 -*-
"""
写接口 · 批 5.1：提交与查询（write_run.py）
==========================================
路线图 §十一 的前 4 个：

| Endpoint | 语义 | 角色 | audit |
| --- | --- | --- | --- |
| `POST /api/runs` | 提交全链 → **202 + run_id** | operator | ✅ |
| `GET /api/runs/{id}` | 查状态/当前阶段/错误 | operator | — |
| `POST /api/handle` | 提交 agent 任务 → 202 | operator | — |
| `GET /api/handle/{id}` | 查轨迹与结论 | operator | — |

**三条设计要点**：

    ① **都走 `hub.queue.commands.enqueue`**（不是一个新队列 ✅）
       → API 和 CLI 入的是**同一个队列** ✅
       → `hub worker` 同时消化 `full` 和 `handle:*` ✅（批 5.0 已接 ✅）

    ② **202 里的 id 是 `pipeline_run.id`**（任务）, **不是** `agent_run.id`（结果）
       前端要两步：先拿 `pipeline_run.id` 轮询状态；
       跑完后 `params.agent_run_id` 里才有结果 id ✅
       为什么要分开：**任务和结果是两个生命周期** ✅
       （任务可能失败/重试/死信，而结果只有一份 ✅）

    ③ **`POST /api/handle` 的 202 是"已排队"，不是"已完成"** ✅
       agent 一次 3~10 秒 → 前端轮询 1~2 次就有结果 ✅
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from hub.api.deps import AuthUser, _trace_id, get_current_user

router = APIRouter(prefix="/api", tags=["write"])


# ════════════════════════════════════════════════════ 请求体
class RunIn(BaseModel):
    kind: str = Field(default="full", description="目前只支持 full")
    params: dict = Field(default_factory=dict)


class HandleIn(BaseModel):
    cluster_id: str = Field(description="簇 UUID 或前 8 位前缀")


# ════════════════════════════════════════════════════ 1. POST /runs
@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def post_run(body: RunIn, request: Request,
             user: AuthUser = Depends(get_current_user)):
    """提交一次全链 → **202**（不阻塞等它跑完 ✅）。

    ⚠️ **重复提交返回 409**：
        `pipeline_run` 有 UNIQUE(kind) WHERE 活跃 的索引 ✅
        `enqueue()` 冲突时返回 1（打印"已经有一条"）✅
        → 这里翻译成 **409 + 已有任务 id** ✅（前端能直接跳过去轮询 ✅）
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import PipelineRun
    from hub.queue.commands import enqueue

    if body.kind != "full":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_KIND",
                             "message": "目前只支持 kind=full（路线图：回填功能未定 ✅）",
                             "trace_id": _trace_id(request)})
    code = enqueue(kind=body.kind, params=body.params)
    if code == 1:
        # 已经有一条在排队/运行 → 409 + 已有 id ✅
        with get_session() as s:
            row = s.scalars(select(PipelineRun).where(
                PipelineRun.kind == body.kind,
                PipelineRun.status.in_(("QUEUED", "RUNNING"))).limit(1)).first()
            existing = str(row.id) if row else None
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"code": "ALREADY_RUNNING",
                             "message": "已经有一条全链在排队/运行中",
                             "run_id": existing,
                             "trace_id": _trace_id(request)})
    if code != 0:
        # 2 = Redis 连不上（链已写库，但没进队列 ✅）
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "QUEUE_UNAVAILABLE",
                             "message": "Redis 连不上；链已写进库，等 worker 对账会捡起来",
                             "trace_id": _trace_id(request)})
    # 拿到刚建的那条 ✅
    with get_session() as s:
        row = s.scalars(select(PipelineRun).where(
            PipelineRun.kind == body.kind,
            PipelineRun.status == "QUEUED").order_by(
                PipelineRun.created_at.desc()).limit(1)).first()
        rid, trace = (str(row.id), row.trace_id) if row else (None, None)
    return {"run_id": rid, "trace_id": trace, "status": "QUEUED",
            "message": "已排队；用 GET /api/runs/{id} 查进度"}


# ════════════════════════════════════════════════════ 2. GET /runs/{id}
@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request,
            user: AuthUser = Depends(get_current_user)):
    """查一条链的状态。**也支持 `full` 之外的 kind**（比如 `handle:*`）✅"""
    from uuid import UUID

    from hub.persistence.db import get_session
    from hub.persistence.models import PipelineRun

    try:
        rid = UUID(run_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "run_id 不是合法 UUID",
                             "trace_id": _trace_id(request)}) from None
    with get_session() as s:
        row = s.get(PipelineRun, rid)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "任务不存在",
                                 "trace_id": _trace_id(request)})
        return {
            "run_id": str(row.id), "kind": row.kind, "status": row.status,
            "trace_id": row.trace_id,
            "current_stage": row.current_stage,
            "attempt": row.attempt,
            "error": row.error,
            "degraded": getattr(row, "degraded", None),
            "params": row.params or {},
            # ⚠️ agent 任务的**结果** id 在这里 ✅（`POST /api/handle` 之后轮询到 DONE 才有 ✅）
            "agent_run_id": (row.params or {}).get("agent_run_id"),
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ════════════════════════════════════════════════════ 3. POST /handle
@router.post("/handle", status_code=status.HTTP_202_ACCEPTED)
def post_handle(body: HandleIn, request: Request,
                user: AuthUser = Depends(get_current_user)):
    """提交一个 agent 任务 → **202**。

    ⚠️ **`kind` 带上簇 id**（批 5.0 的决定 ✅）：
        `pipeline_run` 有 UNIQUE(kind) WHERE 活跃 ✅
        → 同簇只能一条（**这就是幂等** ✅）
        → 不同簇能并行 ✅
    ⚠️ **重复提交同簇 → 409 + 已有 run_id**（不是报错，是"已经有了" ✅）
    """
    from uuid import UUID

    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster, PipelineRun
    from hub.queue.commands import enqueue

    raw = (body.cluster_id or "").strip()
    with get_session() as s:
        cluster = None
        if len(raw) >= 36:
            try:
                cluster = s.get(IssueCluster, UUID(raw))
            except ValueError:
                cluster = None
        if cluster is None and raw:
            # 支持前 8 位前缀 ✅（和 CLI 的 `hub handle 2adf0897` 一致 ✅）
            from sqlalchemy import cast
            from sqlalchemy import Text as SAText
            rows = s.scalars(select(IssueCluster).where(
                cast(IssueCluster.id, SAText).like(f"{raw}%")).limit(2)).all()
            if len(rows) == 1:
                cluster = rows[0]
            elif len(rows) > 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    {"code": "AMBIGUOUS",
                                     "message": f"前缀 {raw} 匹配到多条簇，请给完整 UUID",
                                     "trace_id": _trace_id(request)})
        if cluster is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "CLUSTER_NOT_FOUND",
                                 "message": f"簇不存在：{raw}",
                                 "trace_id": _trace_id(request)})
        cid = str(cluster.id)

    kind = f"handle:{cid[:8]}"
    code = enqueue(kind=kind, params={"cluster_id": cid})
    if code == 1:
        with get_session() as s:
            row = s.scalars(select(PipelineRun).where(
                PipelineRun.kind == kind,
                PipelineRun.status.in_(("QUEUED", "RUNNING"))).limit(1)).first()
            existing = str(row.id) if row else None
        # **幂等**：同簇已有任务 → 409 + 已有 id ✅（不是"报错" ✅）
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"code": "ALREADY_RUNNING",
                             "message": "这个簇已经有一个 agent 任务在排队/运行",
                             "run_id": existing, "trace_id": _trace_id(request)})
    if code != 0:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "QUEUE_UNAVAILABLE",
                             "message": "Redis 连不上；任务已写库，等 worker 对账会捡起来",
                             "trace_id": _trace_id(request)})
    with get_session() as s:
        row = s.scalars(select(PipelineRun).where(
            PipelineRun.kind == kind, PipelineRun.status == "QUEUED").order_by(
                PipelineRun.created_at.desc()).limit(1)).first()
        rid = str(row.id) if row else None
    return {"run_id": rid, "cluster_id": cid, "status": "QUEUED",
            "message": "已排队；用 GET /api/runs/{run_id} 轮询，"
                       "DONE 后 params.agent_run_id 是结果 id"}
