# -*- coding: utf-8 -*-
"""
图片明细 / 人工复核接口（images.py）—— 2026-11-03 新增
=======================================================
**为什么单独一个文件**
    零冲突：**不改** `read.py` / `write_action.py` 一行 ✅
    只在 `main.py` 里挂一个 router ✅

**接口（3 个）**
    GET  /api/images            列图片明细（默认只看待复核的 ✅）
    GET  /api/images/stats      复核队列概况（总数 / 待复核 / 低置信 / 高风险）
    POST /api/images/{id}/review  人工复核（改 / 确认）
    POST /api/images/{id}/unreview 撤销复核

**为什么要复核**（真实业务红线 ✅）
    AI 打的标**不能直接进赔偿决策** ✅
    → 低置信度 / 高风险缺陷 → 人看一眼
    → 人工结果落 `final_defect`，**合并 review_feature 时优先用它**
    → 复核数据**就是下一次微调的标注集** ✅
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from hub.api.deps import AuthUser, _trace_id, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["图片复核"])

#: 允许的复核判定（**和后端 defect_type 的取值域一致** ✅）
ALLOWED_DEFECTS = ("none", "破损", "开线", "色差", "污渍", "其他")


# ══════════════════════════════════════════════════════════════
#  GET /api/images
# ══════════════════════════════════════════════════════════════
@router.get("/images")
def list_images(
    pending_only: bool = Query(True, description="只要还没复核的（默认 True）"),
    max_confidence: float | None = Query(None, ge=0.0, le=1.0,
                                        description="只要置信度 ≤ 这个值的"),
    defect: str | None = Query(None, description="按缺陷类型筛（破损/开线/色差…）"),
    limit: int = Query(50, ge=1, le=500),
    user: AuthUser = Depends(get_current_user),
):
    """列图片级明细（**默认只看待复核的** ✅）。

    每条都带 `source_file`（原文件名）和 `match_tier`（用了哪一级匹配 ✅）
    → 复核时能追到"这是包里哪张图、怎么对上的" ✅
    """
    from hub.multimodal.assets import list_assets

    return {"items": list_assets(pending_only=pending_only,
                                 max_confidence=max_confidence,
                                 defect=defect, limit=limit)}


@router.get("/images/stats")
def image_stats(user: AuthUser = Depends(get_current_user)):
    """复核队列概况：总数 / 待复核 / 低置信 / 高风险。"""
    from hub.multimodal.assets import pending_stats

    return pending_stats()


# ══════════════════════════════════════════════════════════════
#  POST /api/images/{id}/review
# ══════════════════════════════════════════════════════════════
class ReviewIn(BaseModel):
    """复核入参。"""
    final_defect: str = Field(..., description="人工最终判定（确认模型结果就原样传）")
    note: str | None = Field(None, max_length=500,
                             description="备注（**为什么改** —— 这是最有价值的标注信息 ✅）")


@router.post("/images/{asset_id}/review")
def review_image(
    asset_id: str,
    body: ReviewIn,
    request: Request,
    user: AuthUser = Depends(require_role("operator")),
):
    """人工复核一张图（**改 / 确认都走这里** ✅）。

    角色要求：**operator 及以上**（运营有权改标签 ✅）
    副作用：会**重算这条评价的 `review_feature`**（人工优先 ✅）

    错误码：
        400 BAD_ID               id 不是合法 UUID
        404 NOT_FOUND            图片明细不存在
        422 BAD_DEFECT           `final_defect` 不在允许取值内
    """
    tid = _trace_id(request)
    try:
        aid = UUID(asset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "id 不是合法的 UUID",
                             "trace_id": tid}) from None

    if body.final_defect not in ALLOWED_DEFECTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "BAD_DEFECT",
             "message": f"final_defect 只能是 {list(ALLOWED_DEFECTS)}",
             "trace_id": tid})

    from hub.audit import write as audit_write
    from hub.multimodal.assets import review_asset

    try:
        out = review_asset(aid, final_defect=body.final_defect,
                           note=body.note, actor=user.email)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NOT_FOUND", "message": "图片明细不存在",
                             "trace_id": tid}) from None

    audit_write(actor=user.email, action="image.review",
                resource=f"image:{asset_id}",
                detail={"final_defect": body.final_defect,
                        "note": body.note, "trace_id": tid})
    return {"ok": True, **out, "trace_id": tid}


@router.post("/images/{asset_id}/unreview")
def unreview_image(
    asset_id: str,
    request: Request,
    user: AuthUser = Depends(require_role("supervisor")),
):
    """撤销复核（回到模型结果）。**supervisor 及以上** ✅"""
    tid = _trace_id(request)
    try:
        aid = UUID(asset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "id 不是合法的 UUID",
                             "trace_id": tid}) from None

    from hub.multimodal.assets import clear_review
    try:
        out = clear_review(aid)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NOT_FOUND", "message": "图片明细不存在",
                             "trace_id": tid}) from None
    return {"ok": True, **out, "trace_id": tid}
