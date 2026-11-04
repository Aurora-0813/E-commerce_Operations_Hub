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
# 异步模式要返回 **202 + 自定义 body**（装饰器上写死 201 的那个不能用它 ✅）
from fastapi.responses import JSONResponse

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


def _humanize_warnings(warnings: list[str] | None) -> list[dict]:
    """把后端 warnings 变成前端能直接用的形状（2026-11-03 加）。

    **为什么需要**（用户实测："提示信息多采用后端值，没有口语化" ✅）
        ① 后端句子带 Markdown 的 `**`，Vue 模板不认 → **星号原样显示** ✅ ✗
        ② 前端分不清"这是错误还是提醒" ✅
    → 这层做两件事：去掉 `**`，并按关键词给 `code` + `hint`（下一步做什么 ✅）
    """
    out: list[dict] = []
    for raw in warnings or []:
        msg = str(raw).replace("**", "")
        code, hint = "warn", ""
        low = msg.lower()
        if "一行都没成功" in msg or "全坏" in msg:
            code = "ALL_BAD"
            hint = "检查「平台」和「类型」选对没有 —— 京东的文件别选淘宝"
        elif "platform" in low and "错" in msg:
            code = "PLATFORM_MISMATCH"
            hint = "同一个文件换个平台再传一次（重复的行会被跳过）"
        elif "已导过" in msg or "重复" in msg:
            code = "DUPLICATE"
            hint = "这个文件导过了，不会重复入库（按 platform_id upsert）"
        elif "表头" in msg or "缺列" in msg:
            code = "BAD_HEADER"
            hint = "先看「数据接入」页上方的标准表头，改好再传"
        elif "空" in msg:
            code = "EMPTY"
            hint = "文件里没有数据行，检查是不是只导了表头"
        out.append({"code": code, "message": msg, "hint": hint})
    return out


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
        audit_write(actor=user.email, action="data.ingest",
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
            # ⚠️ **2026-11-03 改**：warnings 不再原样倒给前端 ✅
            #    · 去掉 `**`（Markdown 粗体在 Vue 模板里会**原样显示星号** ✅ ✗）
            #    · 每条拆成 `{code, message, hint}`（前端能上色 + 给"下一步做什么" ✅）
            "warnings": _humanize_warnings(warnings[:10]),
            "warnings_text": [w.replace("**", "") for w in warnings[:10]],
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
# ══════════════════════════════════════════════════════════════
#  **库里现有多少数据**（2026-11-03 加）
# ══════════════════════════════════════════════════════════════
# 用户反馈："数据接入模块，库里现在有多少数据 是空的" ✅
#   原来的页面只有上传区 ✅ → 不知道"库里有什么、分析到哪一步了" ✅ ✗
#   这里给一个总览：各表行数 + 最近一次导入 + 最近一次分析状态 ✅
@router.get("/ingest/summary")
def ingest_summary(user: AuthUser = Depends(get_current_user)):
    """数据接入页顶部的「现有数据概览」。

    返回：
        rows:      各业务表的行数（评价/商品/销售/簇/提案/图片明细）
        latest:    最近一条导入批次（文件名/平台/行数/时间）
        analysis:  最近一次全链的状态（RUNNING/DONE + 当前阶段 + 时间）
        hints:     **人话提示**（"还没导入过" / "有数据但没分析过" ✅）
    """
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import (
        ActionProposal, ImageAsset, IngestBatch, IssueCluster, PipelineRun,
        Record, ReviewFeature, SalesDaily,
    )

    with get_session() as s:
        # **当前这一次**的 run（簇/提案只算它 ✅）
        from hub.persistence.current import current_run_id
        _cur_run = current_run_id(s)

        def _n(model, where=None) -> int:
            stmt = select(func.count()).select_from(model)
            if where is not None:
                stmt = stmt.where(where)
            return int(s.scalar(stmt) or 0)

        rows = {
            "record_total": _n(Record),
            "record_review": _n(Record, Record.data_type == "review"),
            "record_product": _n(Record, Record.data_type == "product"),
            "review_feature": _n(ReviewFeature),
            "image_asset": _n(ImageAsset),
            "spu": 50,          # `spu_catalog` 是商品目录，用 count 更快的方式见下
            "sales_daily": _n(SalesDaily),
        # ⚠️ **2026-11-04 加：真实销量（件）**
        #    用户问："评价 7,019 为什么比销售 4,500 多？"
        #    → 因为 `sales_daily` 的一行 = **一个商品在某一天的汇总** ✅
        #      不是"一单" ✅ → 界面写「4,500 行」会被误读成"4,500 单" ✅ ✗
        #    → 真实销量 = sum(units) = **41 万件** → 评价率 1.71% 才对得上 ✅
        "sales_units": int(s.scalar(
            select(func.coalesce(func.sum(SalesDaily.units), 0))) or 0),
        "sales_days": int(s.scalar(
            select(func.count(func.distinct(SalesDaily.stat_date)))) or 0),
            # ⚠️ **簇数只算「当前这一次」**（2026-11-03 修）
            #    每跑一次全链都会插一批新簇、旧的保留当历史 ✅
            #    直接 count 全表 → 显示 241（所有历史总和）✅ ✗ → 用户以为有 241 个问题 ✅
            "clusters": _n(IssueCluster, IssueCluster.run_id == _cur_run),
            "proposals": _n(ActionProposal),
            "batches": _n(IngestBatch),
        }
        # 商品数单独查（`spu_catalog` 表名可能不同 → 用 raw 兜底）
        try:
            from hub.persistence.models import SpuCatalog
            rows["spu"] = _n(SpuCatalog)
        except Exception:                       # noqa: BLE001
            rows["spu"] = 0

        # 最近一条导入
        latest = s.scalars(
            select(IngestBatch).order_by(IngestBatch.created_at.desc()).limit(1)
        ).first()
        latest_out = None if latest is None else {
            "file": latest.file_name, "platform": latest.platform,
            "rows": latest.row_total, "kept": latest.row_kept,
            "status": latest.status,
            "at": latest.created_at.isoformat() if latest.created_at else None,
        }

        # 最近一次全链
        run = s.scalars(
            select(PipelineRun).where(PipelineRun.kind == "full")
            .order_by(PipelineRun.created_at.desc()).limit(1)
        ).first()
        # 注意**两张表**（2026-11-04 修，界面被骗过）
        #    pipeline_run = **队列任务**（跑没跑完，worker 用）
        #    analysis_run = **分析结果批**（产物属于哪一次，外键从 issue_cluster 指过来）
        #    原来只报 pipeline_run，会出现「任务 DEAD 但 20 个簇好好地在那」
        from hub.persistence.models import AnalysisRun
        ar = s.scalars(
            select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(1)
        ).first()
        analysis_out = None if run is None else {
            "run_id": str(run.id), "status": run.status,
            "current_stage": (run.params or {}).get("progress", {}).get("stage")
                              or run.current_stage,
            "stage_pct": (run.params or {}).get("progress", {}).get("pct"),
            "started": run.started_at.isoformat() if run.started_at else None,
            "finished": run.finished_at.isoformat() if run.finished_at else None,
            "degraded": bool(run.degraded),
            # 产物侧（判断分析到底成没成，以它为准）
            "result_id": str(ar.id) if ar else None,
            "result_status": ar.status if ar else None,
        }

    # **人话提示**（前端直接显示 ✅）
    hints: list[str] = []
    if rows["record_review"] == 0:
        hints.append("库里还没有评价数据 —— 先上传一个「评价」类型的文件")
    elif rows["clusters"] == 0:
        hints.append("有评价但还没分析过 —— 点下面的「开始分析」")
    if analysis_out and analysis_out["status"] in ("QUEUED", "RUNNING"):
        _pct = analysis_out.get("stage_pct")
        _st = analysis_out["current_stage"] or "准备中"
        hints.append(f"分析正在跑（{_st}" + (f" {_pct}%" if _pct is not None else "") + "）")
    # 任务状态和产物打架时，明确告诉用户以结果为准（2026-11-04）
    if (analysis_out and rows['clusters'] > 0
            and analysis_out.get('result_status') == 'DONE'
            and analysis_out['status'] in ('DEAD', 'FAILED', 'CANCELLED')):
        hints.append(
            f'分析已完成（{rows["clusters"]} 个问题、{rows["proposals"]} 条提案）—— '
            f'队列任务状态是 {analysis_out["status"]}（历史遗留，结果不受影响）')
    if rows["record_review"] > 0 and rows["clusters"] > 0 and not hints:
        hints.append("数据和分析结果都在 ✅ 有新数据就上传，然后重新分析")

    return {"rows": rows, "latest": latest_out, "analysis": analysis_out,
            "hints": hints}


@router.post("/ingest/images/precheck")
async def precheck_images(
    request: Request,
    file: UploadFile = File(..., description="图片包 zip"),
    allow_loose: bool = Form(False, description="是否允许宽松匹配（后 5 位），默认关"),
    user: AuthUser = Depends(get_current_user),
):
    """上传图片包 -> 只对账 -> 秒回结果（不写库、不解压、不调模型）。

    真实包 428 张 x 8.9 秒 约 63 分钟；先跑再告诉用户只匹配上 1 条 = 白烧一小时。
    所以先预检，用户看清对账再决定跑不跑。

    返回里的 `verdict` 是一句人话结论，前端直接显示。
    """
    tid = _trace_id(request)
    name = (file.filename or "images.zip").strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_FILE_TYPE",
                             "message": "图片包只接受 .zip",
                             "trace_id": tid})

    tmpdir = tempfile.mkdtemp(prefix="hub_pre_")
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
                         "message": f"图片包超过 {MAX_ZIP_BYTES // 1024 // 1024}MB",
                         "trace_id": tid})
                f.write(chunk)
        if total == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                {"code": "EMPTY_FILE", "message": "压缩包是空的",
                                 "trace_id": tid})

        from hub.multimodal.engine import precheck_multimodal
        try:
            st = precheck_multimodal(str(tmp), allow_loose=allow_loose)
        except Exception as e:                  # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                {"code": "PRECHECK_FAILED",
                                 "message": f"{type(e).__name__}: {e}",
                                 "trace_id": tid}) from None

        out = st.as_dict()
        out["file"] = name
        out["bytes"] = total
        out["allow_loose"] = allow_loose
        out["trace_id"] = tid
        return out
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

@router.post("/ingest/images", status_code=status.HTTP_201_CREATED)
async def ingest_images(
    request: Request,
    file: UploadFile = File(..., description="图片包 zip（≤200MB）"),
    vision: str = Form("real", description="real=本地视觉模型 / mock=假标签（只用于测试）"),
    force: bool = Form(False, description="重跑已处理过的评价"),
    limit: int | None = Form(None, description="只处理前 N 条（调试用）"),
    mode: str = Form("sync", description="sync=同步跑（小包/测试）/ queue=入队异步跑（**大包必须用这个** ✅）"),
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

    if mode not in ("sync", "queue"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"code": "BAD_MODE", "message": "mode 只能是 sync / queue",
                             "trace_id": tid})

    # ══════════════════════════════════════════════════════════════
    #  **异步模式（2026-11-03 加）** —— 修"大包必超时"
    # ══════════════════════════════════════════════════════════════
    # **为什么必须加**
    #   同步模式下：428 张 × 8.9 秒 = **63 分钟**，HTTP 请求**必超时** ✅ ✗
    #   而实测 3 张图只要 0.3 秒 → 同步看起来"没问题"，**是个假象** ✅
    # 异步模式：
    #   ① 把 zip **存到稳定路径**（worker 稍后要读，不能用临时目录 ✅）
    #   ② 算整包 sha256 → `kind="mm:<前8位>"`（**同一个包重复传 = 同 kind = 409** ✅ 幂等）
    #   ③ 入队 → **立刻返回 202 + run_id** ✅
    #   ④ 前端轮询 `GET /api/runs/{run_id}`，`DONE` 后 `params.mm` 里就是统计 ✅
    if mode == "queue":
        import hashlib

        # 存到项目根的 `data/uploads/`（worker 稍后要读，不能用临时目录 ✅）
        updir = Path(__file__).resolve().parents[3] / "data" / "uploads"
        updir.mkdir(parents=True, exist_ok=True)
        dst = updir / f"{int(__import__('time').time())}_{name}"
        total_q = 0
        h = hashlib.sha256()
        with open(dst, "wb") as f:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                total_q += len(chunk)
                if total_q > MAX_ZIP_BYTES:
                    dst.unlink(missing_ok=True)
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        {"code": "FILE_TOO_LARGE",
                         "message": f"图片包超过 {MAX_ZIP_BYTES // 1024 // 1024}MB，请分批 ✅",
                         "trace_id": tid})
                h.update(chunk)
                f.write(chunk)
        if total_q == 0:
            dst.unlink(missing_ok=True)
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                {"code": "EMPTY_FILE", "message": "压缩包是空的",
                                 "trace_id": tid})

        sha = h.hexdigest()
        kind = f"mm:{sha[:8]}"

        # ══════════════════════════════════════════════════════════
        #  **已处理过的包 → 直接 409（不重跑 63 分钟 ✅）**
        # ══════════════════════════════════════════════════════════
        # ⚠️ 为什么需要这一段（2026-11-03 实测发现）
        #   `uq_pipeline_run_active` 是 UNIQUE(kind) **只在活跃时**生效 ✅
        #   → 第一条 DONE 之后，**再传同一个包能建新 run** → **重跑一遍** ✅ ✗
        #   → 428 张图 = 白等 63 分钟 ✅
        # 语义：
        #   `force=False`（默认）→ 处理过就 409 + 已有 run_id ✅
        #   `force=True`        → 允许重跑（用户明确要求 ✅）
        if not force:
            from sqlalchemy import select as _sel0

            from hub.persistence.db import get_session as _gs0
            from hub.persistence.models import PipelineRun as _PR0
            with _gs0() as _s0:
                prev = _s0.scalars(
                    _sel0(_PR0).where(_PR0.kind == kind)
                    .order_by(_PR0.created_at.desc()).limit(1)).first()
                if prev is not None and prev.status in (
                        "DONE", "DEGRADED", "QUEUED", "RUNNING"):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        {"code": "ALREADY_PROCESSED",
                         "message": (f"这个图片包（sha={sha[:8]}）已经处理过了 ✅ "
                                     f"想强制重跑就勾上「重跑已处理的」"),
                         "run_id": str(prev.id),
                         "status": prev.status,
                         "trace_id": tid})

        from hub.queue.commands import enqueue
        code = enqueue(kind=kind, params={
            "images": str(dst), "vision": vision, "force": force,
            "allow_loose": False, "limit": limit,
        })
        if code == 1:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                {"code": "ALREADY_RUNNING",
                                 "message": f"这个图片包（sha={sha[:8]}）已经在排队/处理中 ✅",
                                 "trace_id": tid})
        if code != 0:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                {"code": "QUEUE_UNAVAILABLE",
                                 "message": "Redis 连不上；任务已写库，等 worker 对账会捡起来",
                                 "trace_id": tid})

        # 回查刚建的那条（**不能加 status=='QUEUED'** —— worker 可能已经抢走了 ✅）
        from sqlalchemy import select as _sel

        from hub.persistence.db import get_session as _gs
        from hub.persistence.models import PipelineRun as _PR
        with _gs() as _s:
            row = _s.scalars(_sel(_PR).where(_PR.kind == kind)
                             .order_by(_PR.created_at.desc()).limit(1)).first()
            rid = str(row.id) if row else None

        # **清理旧包**（真包 471MB 一个，不清理会堆满磁盘 ✅）
        try:
            olds = sorted(updir.glob("*.zip"), key=lambda f: f.stat().st_mtime,
                          reverse=True)
            for f in olds[3:]:                 # 只留最近 3 个 ✅
                f.unlink(missing_ok=True)
        except Exception as e:                 # noqa: BLE001
            print(f"  [清理] 旧图片包清理失败：{e}")

        from hub.audit import write as audit_write
        audit_write(actor=user.email, action="data.ingest_images",
                    resource=f"run:{rid}",
                    detail={"file": name, "bytes": total_q, "sha256": sha,
                            "mode": "queue", "vision": vision, "trace_id": tid})
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
            "run_id": rid, "kind": kind, "file": name, "bytes": total_q,
            "sha256": sha, "status": "QUEUED", "mode": "queue",
            "message": "已入队；用 GET /api/runs/{run_id} 轮询，DONE 后 params.mm 是统计",
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
        audit_write(actor=user.email, action="data.ingest_images",
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
