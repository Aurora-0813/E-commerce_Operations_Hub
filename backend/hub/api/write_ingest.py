# -*- coding: utf-8 -*-
"""
写接口 · 数据上传（write_ingest.py）—— 2026-11-01 补
====================================================
**为什么补这个**（用户发现的真缺口）

    整个系统只能**命令行**导入数据：
        hub p0 <文件> --platform taobao --type review|product|sales
    而 **42 条 API 路由里没有任何上传入口** ✅ ✗
        → 前端无法上传
        → 用户以为"只能用演示数据提前塞进去"

**而链路本身是通的**（我一开始误判了，这里更正）
    `read_file()` → `(RawBatch, records, MappingReport)`
        → `IngressRepo().save(batch, records, report)` → `(batch_id, existed)`
        → `repo_ingress` 里 **sales → sales_daily** 的转换也在 ✅
    → 实测 `sales_daily` **有 4500 行**（2026-07-25 ~ 2026-10-22 ✅）
    **所以缺的只是"一个能上传的门"** ✅

**四个设计选择（都说清为什么）**

    ① **同步，不做队列**
       一个 4000 行的 xlsx 解析 + 入库约 **3~8 秒** ✅
       走队列反而要多一轮轮询；等它长到 10 万行再改异步 ✅

    ② **文件大小上限 20MB**（`MAX_BYTES`）
       不设上限 → 一个 1GB 文件把进程内存吃爆 ✅ ✗
       20MB 对"一个月导出"足够（实测 sales 90 天 = 4500 行 ≈ 400KB ✅）

    ③ **幂等由 `IngressRepo.save` 保证**（同一个文件 SHA256 只导一次 ✅）
       → 重复上传同一个文件 → 返回 `existed=True` + 原来的 batch_id（**不是报错** ✅）

    ④ **`platform` / `data_type` 白名单**
       拼错一个字母（`taoboa`）就会写出 434 条脏 record ✅ ✗
       → 用 Enum 卡死（**400 而不是脏数据** ✅）
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from hub.api.deps import AuthUser, _trace_id, get_current_user

router = APIRouter(prefix="/api", tags=["write-ingest"])

# 20MB ✅ —— 不够就改，但要同步改这里和文档
MAX_BYTES = 20 * 1024 * 1024
# 图片包单独限（图片比表格大得多 ✅）
# ⚠️ 2026-11-01 实测：真实的 `images.zip` 有 **471MB**（428 张图）
#    → 200MB 上限会直接 413 ✅ ✗ → 提到 1GB
#    仍然**流式写临时文件**（不读进内存），所以 1GB 也不会把进程吃爆 ✅
MAX_ZIP_BYTES = 1024 * 1024 * 1024
CHUNK = 1024 * 1024

ALLOWED_PLATFORM = ("taobao", "jd")
ALLOWED_TYPE = ("review", "product", "sales")


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest(
    request: Request,
    file: UploadFile = File(..., description="平台导出的 xlsx（≤20MB）"),
    platform: str = Form(..., description="taobao / jd"),
    data_type: str = Form(..., description="review / product / sales"),
    user: AuthUser = Depends(get_current_user),
):
    """**上传一个平台导出文件 → 解析 → 落库 → 返回质量报告。**

    · 重复上传同一个文件 → **`existed: true` + 原来的 `batch_id`**（幂等 ✅，不是报错）
    · 文件超过 20MB → **413**
    · `platform` / `data_type` 写错 → **400**（**不让脏数据进库** ✅）
    · 解析过程中的坏行 → **`report.row_bad`**（不会因为几行坏就整体失败 ✅）
    """
    tid = _trace_id(request)

    # ---------- ① 白名单（先卡住，别让脏数据进库）----------
    if platform not in ALLOWED_PLATFORM:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_PLATFORM",
                             "message": f"platform 只能是 {ALLOWED_PLATFORM}，收到 {platform!r}",
                             "trace_id": tid})
    if data_type not in ALLOWED_TYPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_DATA_TYPE",
                             "message": f"data_type 只能是 {ALLOWED_TYPE}，收到 {data_type!r}",
                             "trace_id": tid})

    name = (file.filename or "upload.xlsx").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_FILE_TYPE",
                             "message": "只接受 .xlsx（平台导出的都是 xlsx ✅）",
                             "trace_id": tid})

    # ---------- ② 流式写临时文件（**不一次读进内存** ✅）----------
    tmpdir = tempfile.mkdtemp(prefix="hub_ingest_")
    tmp = Path(tmpdir) / name
    total = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        {"code": "FILE_TOO_LARGE",
                         "message": f"文件超过 {MAX_BYTES // 1024 // 1024}MB，"
                                    "请按月拆包后再传 ✅",
                         "trace_id": tid})
                f.write(chunk)
        if total == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                {"code": "EMPTY_FILE", "message": "文件是空的",
                                 "trace_id": tid})

        # ---------- ③ 解析 + 落库（复用 CLI 同一条路径 ✅）----------
        from hub.audit import write as audit_write
        from hub.ingress.adapter import read_file
        from hub.persistence.repo_ingress import IngressRepo

        try:
            batch, records, report = read_file(tmp, platform, data_type)
        except Exception as e:                # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                {"code": "PARSE_FAILED",
                                 "message": f"解析失败：{type(e).__name__}: {e}",
                                 "trace_id": tid}) from None

        batch_id, existed = IngressRepo().save(batch, records, report)

        # ---------- ④ 审计（**每次上传都记** ✅）----------
        audit_write(actor=f"user:{user.id}", action="data.ingest",
                    resource=f"ingest_batch:{batch_id}",
                    detail={"file": name, "platform": platform,
                            "data_type": data_type, "bytes": total,
                            "rows": len(records), "bad": report.row_bad,
                            "existed": existed},
                    trace_id=tid)

        # ⚠️ **一行都没成功时，必须给出人话原因**（2026-11-01 测试暴露）
        #    实测：把 `jd` 的销售文件当 `platform=taobao` 传 → **2250 行全坏**
        #    而 `report.warnings` 是**空的** ✅ ✗
        #    → 用户看到 `rows_ok: 0` 不知道怎么办 ✅
        warnings = list(getattr(report, "warnings", []) or [])
        if report.row_ok == 0 and report.row_bad > 0:
            warnings.insert(0,
                            f"**一行都没成功**（{report.row_bad} 行全坏）。"
                            f"最常见的原因是 **platform 传错** —— "
                            f"这个文件可能不是「{platform}」的导出 ✅ "
                            f"下一步：确认文件名里的平台，再用对应的 platform 重传 ✅")
        return {
            "ok": True,
            "batch_id": str(batch_id),
            "existed": existed,           # **true = 该文件已导过，本次跳过**
            "file": name,
            "bytes": total,
            "platform": platform,
            "data_type": data_type,
            "rows_ok": report.row_ok,
            "rows_bad": report.row_bad,
            "pii_dropped": getattr(report, "pii_stats", {}) or {},
            "unmapped_rate": getattr(report, "unmapped_rate", None),
            "warnings": warnings[:10],
            "message": ("该文件已导入过（同一个 SHA256），本次没重复写 ✅"
                        if existed else
                        (f"已入库 {len(records)} 条 ✅" if report.row_ok
                         else "解析完成但**一行都没入库** —— 看 warnings ✅")),
        }
    finally:
        # **临时文件必须删**（里面有评价原文 / PII ✅）
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  ② 图片包上传（2026-11-01 补）—— **这是另一条链路** ✅
# ══════════════════════════════════════════════════════════════
# **为什么单独一个接口而不是塞进 /api/ingest**
#   ① 输入不同：这是 **zip**（图片包），不是 xlsx ✅
#   ② 处理完全不同：`/api/ingest` 是"读表落库"，这个是 **M2 多模态**：
#        解 zip → 沙箱 → pHash 去重 → Vision 打标 → 元数据剥离 → **覆写销毁原图**
#   ③ 耗时差一个量级：xlsx 3~8 秒；**图片包 50 张要 50 次 Vision**（几分钟 ✅）
#   ④ 合规红线：**图片只在本地沙箱处理，永不出网** ✅
#      `multimodal/vision.py` 硬编码只连本地，`cloud_guard` 对图片直接抛异常 ✅
@router.post("/ingest/images", status_code=status.HTTP_201_CREATED)
async def ingest_images(
    request: Request,
    file: UploadFile = File(..., description="图片包 zip（≤200MB）"),
    vision: str = Form("real", description="real=本地视觉模型 / mock=假标签（只用于测试）"),
    force: bool = Form(False, description="重跑已处理过的评价"),
    limit: int | None = Form(None, description="只处理前 N 条（调试用）"),
    user: AuthUser = Depends(get_current_user),
):
    """**上传图片包 zip → M2 多模态处理 → 落 `review_feature`。**

    · 只接受 `.zip`；**≤ 200MB**
    · `vision="real"` 会调本地 `qwen2.5vl:3b`（**慢，50 张约几分钟** ✅）
    · `vision="mock"` 给假标签（**只用于连通性测试**，别拿它当结果 ✅）
    · **图片永不入库、永不出网** ✅ —— 处理完沙箱里的原图被覆写销毁 ✅
    · 返回里 `residual` **必须是 0**（沙箱残留），不是 0 就是 bug ✅
    """
    tid = _trace_id(request)

    if vision not in ("real", "mock"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_VISION",
                             "message": "vision 只能是 real / mock",
                             "trace_id": tid})

    name = (file.filename or "images.zip").strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_FILE_TYPE",
                             "message": "图片包只接受 .zip（把图片打包成 zip 再传 ✅）",
                             "trace_id": tid})

    # ---------- 流式写临时文件（200MB 不能一次读进内存 ✅）----------
    tmpdir = tempfile.mkdtemp(prefix="hub_img_")
    tmp = Path(tmpdir) / name
    total = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        {"code": "FILE_TOO_LARGE",
                         "message": f"图片包超过 {MAX_ZIP_BYTES // 1024 // 1024}MB，请分批打包 ✅",
                         "trace_id": tid})
                f.write(chunk)
        if total == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                {"code": "EMPTY_FILE", "message": "压缩包是空的",
                                 "trace_id": tid})

        # ---------- 跑 M2（**和 `hub mm` 同一个函数** ✅）----------
        from hub.audit import write as audit_write
        from hub.multimodal.engine import run_multimodal
        from hub.multimodal.vision import get_vision

        try:
            v = get_vision(vision)
        except Exception as e:                # noqa: BLE001
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                {"code": "VISION_UNAVAILABLE",
                                 "message": f"视觉模型不可用：{type(e).__name__}: {e}",
                                 "trace_id": tid}) from None

        try:
            stats = run_multimodal(str(tmp), force=force, vision=v,
                                   limit=limit)
        except Exception as e:                # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                {"code": "MULTIMODAL_FAILED",
                                 "message": f"{type(e).__name__}: {e}",
                                 "trace_id": tid}) from None

        # ---------- 审计（**图片处理必须留痕** ✅）----------
        audit_write(actor=f"user:{user.id}", action="data.ingest_images",
                    resource=f"trace:{stats.trace_id}",
                    detail={"file": name, "bytes": total, "vision": v.name,
                            "images": stats.images, "deduped": stats.deduped,
                            "processed": stats.processed, "failed": stats.failed,
                            "destroyed": stats.destroyed,
                            "residual": stats.residual},
                    trace_id=tid)

        return {
            "ok": True,
            "file": name,
            "bytes": total,
            "vision": v.name,
            "trace_id": stats.trace_id,
            "images": stats.images,          # 包里有几张图
            "deduped": stats.deduped,        # pHash 去重掉几张
            "processed": stats.processed,    # 成功处理几条评价
            "skipped": stats.skipped,
            "failed": stats.failed,
            "stripped": stats.stripped,      # 元数据剥离（GPS/设备信息）
            "destroyed": stats.destroyed,    # 覆写销毁几个文件
            "residual": stats.residual,      # **必须是 0** ✅
            "elapsed_s": stats.elapsed_s,
            "message": ("图片包处理完成 ✅" if stats.residual == 0
                        else f"⚠️ 沙箱残留 {stats.residual} 个文件 —— 这是 bug，要查 ✅"),
        }
    finally:
        # **必须删**：图片是合规红线（`cloud_guard` 对图片直接拒 ✅）
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
