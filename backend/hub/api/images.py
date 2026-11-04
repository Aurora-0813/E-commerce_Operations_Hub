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
from fastapi.responses import Response
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


# ══════════════════════════════════════════════════════════════
#  **取一张图给复核看**（2026-11-03 加）—— 你质疑最重的一条
# ══════════════════════════════════════════════════════════════
# 用户反馈："如果要（人工审核），为什么前端不显示图片？"
#   ↓
# **矛盾点**：M2 的设计是「图片阅后即焚」——不落库、不出网 ✅
#   所以复核页**没有图可显示** ✅ ✗
#   → 但人工复核**必须看图**（否则复核个啥）✅
# **解法**：**按需临时取一张**
#   ① 从原始图片包（`data/uploads/*.zip` 或 `data/mock/images/`）里读出来
#   ② **只在内存里过一道**（不写盘、不落库 ✅）
#   ③ 响应头带 `Cache-Control: no-store`（浏览器也不缓存 ✅）
#   ④ 只按**文件名**取（`Path(name).name`）→ 防路径穿越 ✅
#   ⑤ 单张上限 5MB（防有人塞巨图 ✅）
#   ↓
#   这既守住「不持久化原图」，又让复核真的能看图 ✅
#   （面试可以这么说："我们不留图，但复核时**临时给看一眼**，看完连内存都不留 ✅"）
THUMB_MAX_BYTES = 5 * 1024 * 1024
THUMB_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp"}


def _find_in_packages(name: str) -> tuple[bytes, str] | None:
    """按文件名在图片包里找一张图 → `(bytes, media_type)`。找不到返回 None。"""
    from pathlib import Path as _P

    import zipfile as _zip

    safe = _P(name).name                 # **只取文件名**（防 ../ ✅）
    media = THUMB_TYPES.get(_P(safe).suffix.lower())
    if media is None:
        return None

    root = _P(__file__).resolve().parents[3]      # 项目根
    # ① 上传目录里的 zip（**新的优先** ✅）
    updir = root / "data" / "uploads"
    if updir.exists():
        for z in sorted(updir.glob("*.zip"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                with _zip.ZipFile(z) as zf:
                    for inner in zf.namelist():
                        if _P(inner).name == safe:
                            data = zf.read(inner)
                            if len(data) <= THUMB_MAX_BYTES:
                                return data, media
            except Exception:            # noqa: BLE001  坏了就试下一个 ✅
                continue
    # ② 本地 mock 图片（开发/演示 ✅）
    for cand in (root / "data" / "mock" / "images", root / "data" / "demo_new" / "images"):
        f = cand / safe
        if f.exists() and f.stat().st_size <= THUMB_MAX_BYTES:
            return f.read_bytes(), media
    return None


@router.get("/images/{asset_id}/thumb")
def image_thumb(asset_id: str, user: AuthUser = Depends(get_current_user)):
    """**复核时临时取一张图**（不落库、不写盘、不缓存 ✅）。

    找不到时返回 **404** + `hint`（告诉你"原图包已经不在了" ✅）
    """
    from uuid import UUID as _UUID

    from hub.persistence.db import get_session
    from hub.persistence.models import ImageAsset

    try:
        aid = _UUID(asset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_ID", "message": "id 不是合法 UUID"}) from None

    with get_session() as s:
        a = s.get(ImageAsset, aid)
        if a is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                {"code": "NOT_FOUND", "message": "图片明细不存在"}) from None
        name = a.source_file or ""

    if not name:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            {"code": "NO_SOURCE",
                             "message": "这条明细没记录原文件名，取不到图"}) from None

    hit = _find_in_packages(name)
    if hit is None:
        # ⚠️ **不返回 404**（2026-11-03 改）
        #    404 会让**浏览器控制台每条都报红** ✅（网络层报错，JS 拦不住 ✅）
        #    → 20 张缩略图 = 20 条红色 404 ✅ ✗
        #    改成：返回一张**灰色占位图** + 头 `X-Thumb-Missing: 1`
        import io as _io

        from PIL import Image as _I
        buf = _io.BytesIO()
        _I.new("RGB", (120, 120), (245, 247, 250)).save(buf, "JPEG", quality=60)
        return Response(content=buf.getvalue(), media_type="image/jpeg", headers={
            "Cache-Control": "no-store",
            "X-Thumb-Missing": "1",
            "X-Thumb-Reason": "IMAGE_GONE",
        })

    data, media = hit
    return Response(content=data, media_type=media, headers={
        # **三个不缓存**（浏览器 / 代理 / 本地 ✅）
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    })


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
