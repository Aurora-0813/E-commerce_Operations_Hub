# -*- coding: utf-8 -*-
"""
写接口 · 批 5.3：通知/知识/导出/死信（write_misc.py）
==================================================
路线图 §十一 的最后 5 个：

| Endpoint | 语义 | 角色 | audit |
| --- | --- | --- | --- |
| `POST /api/notify/{id}/read` | 标记通知已读 | operator | — |
| `POST /api/knowledge` | 新建知识条目（DRAFT） | operator | ✅ |
| `POST /api/knowledge/{id}/publish` | 审核发布（→ 注入提示词） | **supervisor** | ✅ |
| `GET /api/export/{kind}` | 导出 xlsx（reviews / clusters / retro） | operator | — |
| `POST /api/dlq/replay` | 重放死信（**危险**） | **supervisor** | ✅ |

**三个设计要点**：

    ① **通知"已读"没有 `read_at` 列**（2026-11-01 核对 ✅）
       → 用 `status="READ"` ✅（不新建列 ✅ 路线图：表全都在 ✅）

    ② **导出返回文件流**（`StreamingResponse`）
       → 写到临时文件再删 ✅（不落 `out/` —— 那是 CLI 的目录 ✅）
       → **PII 已在 `report.export_*` 里复查过** ✅（阶段 3.9 ✅）

    ③ **死信重放要 supervisor + confirm**（路线图标了"危险" ✅）
       → 重放 = "把已经失败的东西再跑一遍" ✅
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from hub.api.deps import AuthUser, _trace_id, get_current_user
# `_require` 现在也在 `deps` 里（write_action 只是复用它 ✅），
# 这里从 deps 导入，避免 write_misc → write_action 的多余依赖 ✅
from hub.api.deps import require_role as _require_role


def _require(user, min_role, request):
    from hub.tools.spec import CallContext
    from hub.api.deps import _trace_id
    if not CallContext(role=user.role).has_role(min_role):
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            {'code': 'ROLE_TOO_LOW',
                             'message': f'需要 {min_role} 及以上角色，你是 {user.role}',
                             'trace_id': _trace_id(request)})

router = APIRouter(prefix="/api", tags=["write-misc"])


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    topic: str | None = Field(default=None, description="限定某个问题类型；空=全局")


class ReplayIn(BaseModel):
    confirm: bool = Field(default=False, description="危险操作二次确认")
    run_id_prefix: str = Field(min_length=4, max_length=36)


# ════════════════════════════════════════════════════ 1. notify read
@router.post("/notify/{nid}/read")
def notify_read(nid: int, request: Request,
                user: AuthUser = Depends(get_current_user)):
    """标记一条通知已读。

    ⚠️ `notification` **没有 `read_at` 列**（2026-11-01 核对 ✅）
       → 用 `status="READ"` ✅（不新建列，路线图：表全都在 ✅）
    """
    from hub.persistence.db import get_session
    from hub.persistence.models import Notification

    with get_session() as s:
        n = s.get(Notification, int(nid))
        if n is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "通知不存在",
                                 "trace_id": _trace_id(request)})
        was = n.status
        n.status = "READ"
        s.commit()
    return {"ok": True, "id": int(nid), "was": was, "now": "READ"}


# ════════════════════════════════════════════════════ 2. knowledge create
@router.post("/knowledge", status_code=status.HTTP_201_CREATED)
def knowledge_create(body: KnowledgeIn, request: Request,
                     user: AuthUser = Depends(get_current_user)):
    """新建知识条目 → **DRAFT**（人审发布后才注入 ✅）。"""
    from hub.audit import write as audit_write
    from hub.knowledge import create as k_create

    kid = k_create(body.title, body.content, topic=body.topic)
    audit_write(actor=f"user:{user.id}", action="knowledge.create",
                resource=f"knowledge:{kid}",
                detail={"title": body.title, "topic": body.topic},
                trace_id=_trace_id(request))
    return {"ok": True, "id": kid, "status": "DRAFT",
            "note": "DRAFT 不生效；发布后才会注入提示词 ✅"}


# ════════════════════════════════════════════════════ 3. knowledge publish
@router.post("/knowledge/{kid}/publish")
def knowledge_publish(kid: str, request: Request,
                      user: AuthUser = Depends(get_current_user)):
    """审核发布（**supervisor** ✅）→ PUBLISHED + version+1 → 立即注入 ✅"""
    _require(user, "supervisor", request)
    from hub.audit import write as audit_write
    from hub.knowledge import publish as k_publish

    try:
        r = k_publish(kid)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NOT_FOUND", "message": str(e),
                             "trace_id": _trace_id(request)}) from None
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": str(e),
                             "trace_id": _trace_id(request)}) from None
    if not r.get("ok"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"code": "ALREADY_PUBLISHED",
                             "message": r.get("reason", ""),
                             "trace_id": _trace_id(request)})
    audit_write(actor=f"user:{user.id}", action="knowledge.publish",
                resource=f"knowledge:{kid}",
                detail={"version": r.get("version")},
                trace_id=_trace_id(request))
    return {"ok": True, "id": kid, "status": "PUBLISHED",
            "version": r.get("version")}


# ════════════════════════════════════════════════════ 4. export
@router.get("/export/{kind}")
def export(kind: str, request: Request,
           user: AuthUser = Depends(get_current_user)):
    """导出 xlsx。`kind` ∈ `reviews` / `clusters` / `retro` ✅

    ⚠️ 写到**临时文件**再返回（不落 `out/` —— 那是 CLI 的目录 ✅）
    ⚠️ PII 已在 `report.export_*` 里**脱敏 + 计数** ✅（阶段 3.9 ✅）
    """
    import shutil

    from hub import report as R

    tmpdir = tempfile.mkdtemp(prefix="hub_export_")
    p = Path(tmpdir) / f"{kind}.xlsx"
    try:
        if kind == "reviews":
            meta = R.export_negative_reviews(p)
        elif kind == "clusters":
            meta = R.export_clusters(p)
        elif kind == "retro":
            meta = R.export_retro(p)
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                {"code": "BAD_KIND",
                                 "message": "kind 只能是 reviews / clusters / retro",
                                 "trace_id": _trace_id(request)})
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    def _iter():
        with open(p, "rb") as f:
            yield from f
        # 发完就删 ✅（临时目录不留垃圾 ✅）
        shutil.rmtree(tmpdir, ignore_errors=True)

    return StreamingResponse(
        _iter(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{kind}.xlsx"',
            # 把"导出了多少行 / 脱敏几处"放在响应头里（前端能显示 ✅）
            "X-Export-Rows": str(meta.get("rows", 0)),
            "X-Export-Pii-Redacted": str(meta.get("pii_redacted", 0)),
        },
    )


# ════════════════════════════════════════════════════ 5. dlq replay
@router.post("/dlq/replay")
def dlq_replay(body: ReplayIn, request: Request,
               user: AuthUser = Depends(get_current_user)):
    """重放死信（**supervisor + confirm** ✅ —— 路线图标了"危险" ✅）。"""
    _require(user, "supervisor", request)
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "CONFIRM_REQUIRED",
                             "message": "危险操作需要 confirm: true",
                             "trace_id": _trace_id(request)})
    from hub.queue.commands import replay as dlq_replay_fn
    try:
        code = dlq_replay_fn(body.run_id_prefix)
    except Exception as e:                        # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "REPLAY_FAILED",
                             "message": f"{type(e).__name__}: {e}",
                             "trace_id": _trace_id(request)}) from None
    # 0 = 成功；1 = 没找到（不是错误，是"没有这条死信" ✅）
    return {"ok": code == 0, "exit_code": code,
            "message": "已重新入队" if code == 0 else "没找到匹配的死信"}
