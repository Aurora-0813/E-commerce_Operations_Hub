# -*- coding: utf-8 -*-
"""
M2 多模态主流程（engine.py）
===========================
读 record（has_image=true）→ 图片包 → 沙箱 → pHash 去重 → Vision → 销毁 → 写 review_feature。

为什么按"批次"处理：
    和 DQE 一样，一次处理一个批次，方便追溯"这批图片什么时候处理的"。

图片来源（对应真实业务：商家上传）：
    - 目录（开发/测试）
    - zip  （模拟商家上传的压缩包）
"""
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from hub.contracts import VisionTags
from hub.core.tracing import traced
from hub.multimodal.matching import (
    TIER_TEXT, PackageIndex, PrecheckStats, list_package, resolve_images,
)
from hub.multimodal.phash import phash
from hub.multimodal.sandbox import Sandbox
from hub.multimodal.text_hint import infer_defect_from_text
from hub.multimodal.vision import VisionModel, get_vision
from hub.persistence.db import get_session
from hub.persistence.models import Record as RecordRow
from hub.persistence.models import ReviewFeature
from hub.persistence.upsert import upsert_all


@dataclass
class MultimodalStats:
    """一次 M2 运行的统计。

    注意单位：total / processed / skipped 是**评价数**，
          images / deduped / failed 是**图片数**（一条评价可能 1~9 张）。
    """
    total: int = 0        # 待处理评价数
    processed: int = 0    # 成功处理的评价数
    skipped: int = 0      # 跳过（没图 / 图片包里找不到）
    images: int = 0       # 实际处理的图片张数
    deduped: int = 0      # pHash 命中的图片数（省掉的模型调用）
    failed: int = 0       # Vision 调用失败的图片数（已降级）
    stripped: int = 0     # 成功剥离元数据的张数（隐私红线）
    destroyed: int = 0    # 覆写销毁的文件数
    residual: int = 0     # 销毁后沙箱残留（应为 0）
    matched: int = 0       # 匹配上图的评价数（2026-11-03 加）
    unmatched: int = 0     # 一条图都没匹配上的评价数
    assets: int = 0        # 写进 image_asset 的图片级明细条数
    by_tier: dict = field(default_factory=dict)   # 各级命中多少条评价
    trace_id: str = ""    # 这次运行的 trace_id（写进 analysis_run，用于审计）
    elapsed_s: float = 0.0


@traced("m2.multimodal", kind="vision")
def run_multimodal(
    image_package: str | Path,
    *,
    batch_id: UUID | None = None,
    force: bool = False,
    vision: VisionModel | None = None,
    limit: int | None = None,
    allow_loose: bool = False,
) -> MultimodalStats:
    """跑一次多模态处理。

    :param image_package: 图片包路径（目录或 zip）
    :param batch_id:      只处理这个批次（None = 全部）
    :param force:         True = 重处理（覆盖已有 review_feature）
    :param vision:        自定义 Vision（默认 MockVision）
    :param limit:         只处理前 N 条（本地模型慢，先抽检用）
    :param allow_loose:   **是否允许「后 5 位后缀」这种宽松匹配**（默认 False ✅
                          因为 `TB00223` 和 `JD00223` 后 5 位都是 `00223` ✅ 会误伤）
    """
    t0 = time.time()

    rows = _fetch_rows(batch_id, force)
    if limit:
        rows = rows[:limit]
    stats = MultimodalStats(total=len(rows))
    if not rows:
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    v = vision or get_vision("mock")
    seen: dict[str, VisionTags] = {}     # phash → tags（同一张图只调一次模型）
    features: list[dict] = []            # 待批量写库（review_feature，聚合 ✅）
    assets: list[dict] = []              # 待批量写库（image_asset，图片级 ✅）

    # **先列包里的文件清单**（不解压，秒回 ✅）—— 分级匹配要用它
    pkg: PackageIndex = list_package(image_package)
    # **整包 sha256**（2026-11-03 加）：幂等判重 + 审计溯源 ✅
    zip_sha = _package_sha256(image_package)

    with Sandbox() as sb:
        imported = sb.ingest(image_package)
        print(f"沙箱导入 {imported} 张图片，待处理 {len(rows)} 条评价")
        if pkg.manifest:
            print(f"  发现 manifest：{pkg.manifest_source}（{len(pkg.manifest)} 行）✅")

        for row in rows:
            # 1. 没图 → 跳过
            if not row.image_urls:
                stats.skipped += 1
                continue

            # 2. 逐张处理（一条评价最多 9 张图）
            image_tags: list[tuple[VisionTags, str, str]] = []   # (tags, phash, sha256)
            degraded = False

            # ⚠️ **2026-11-03 改：分级匹配**（原来只有 `sb.find(Path(url).name)` ✅）
            #    见 `matching.py` 的说明：只按 URL 文件名匹配，商家手动打包时一张都对不上
            res = resolve_images(
                platform_review_id=row.platform_review_id,
                image_urls=row.image_urls,
                index=pkg,
                allow_loose=allow_loose,
            )
            if res.tier:
                stats.by_tier[res.tier] = stats.by_tier.get(res.tier, 0) + 1

            for fn in res.files:
                path = sb.find(fn)
                if path is None:
                    continue        # 这张图不在包里，跳过，不影响其他图

                h = phash(str(path))
                if h in seen:
                    tags = seen[h]
                    stats.deduped += 1
                else:
                    try:
                        tags = v.tag(str(path))
                    except Exception as e:      # noqa: BLE001 单张失败不能拖垮整批
                        print(f"  [降级] Vision 失败：{e}")
                        tags = VisionTags(
                            defect_type="其他", severity=0,
                            color_deviation=0.0, confidence=0.0,
                        )
                        degraded = True
                        stats.failed += 1
                    seen[h] = tags

                image_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                image_tags.append((tags, h, image_sha))
                stats.images += 1
                # **图片级明细**（2026-11-03 加）：单张的标签 + 溯源 ✅
                assets.append({
                    "review_id": row.id,
                    # ⚠️ 行级批次要**从 record 上取**（2026-11-03 修）
                    #    原来直接用 `batch_id` 参数 → 不传就是 None
                    #    → 图片明细**追不到是哪个批次导的** ✅ ✗
                    "batch_id": getattr(row, "batch_id", None) or batch_id,
                    "source_file": path.name,
                    "match_tier": res.tier,
                    "phash_bit": h,
                    "sha256": image_sha,
                    "defect_type": tags.defect_type,
                    "severity": tags.severity,
                    "color_deviation": tags.color_deviation,
                    "confidence": tags.confidence,
                    "vision_model": v.name,
                    "degraded": degraded,
                })

            if not image_tags:
                stats.skipped += 1    # 所有图都不在包里
                stats.unmatched += 1
                continue

            # 3. 多图聚合：取最严重的一张
            tags, h, image_sha = _aggregate(image_tags)

            # 3.5 跨模态补类别：图片判成"其他"时，用评价文字定性
            #     图片负责"发现有没有问题"，文字负责"是什么问题"
            model_tag = v.name
            if tags.defect_type == "其他" and not degraded:
                hinted = infer_defect_from_text(row.content_clean)
                if hinted:
                    tags = tags.model_copy(update={"defect_type": hinted})
                    model_tag = f"{v.name}+text"

            # 4. 收集待写数据
            features.append({
                "review_id": row.id,
                "defect_type": tags.defect_type,
                "severity": tags.severity,
                "color_deviation": tags.color_deviation,
                "ocr_text": tags.ocr_text,
                "vision_confidence": tags.confidence,
                "phash_bit": h,
                "image_sha256": image_sha,
                "vision_model": model_tag,
                "degraded": degraded,
            })
            stats.processed += 1
            stats.matched += 1

    # with 退出 → 沙箱覆写销毁
    stats.destroyed = sb.destroyed
    stats.residual = sb.residual()
    stats.stripped = sb.stripped

    # 批量写 review_feature（聚合层 ✅）
    _write_features(features)
    # 批量写 image_asset（图片级 ✅ 2026-11-03 加）
    _write_assets(assets)
    stats.assets = len(assets)

    stats.elapsed_s = round(time.time() - t0, 2)

    # 写运行记录（审计）：没有这一条，我们只是在"声称合规"，不是"证明合规"
    stats.trace_id = _write_run(stats, v.name, zip_sha)

    return stats


def _write_run(stats: "MultimodalStats", vision_model: str,
               zip_sha256: str = "") -> str:
    """写一条 analysis_run，作为 M2 的审计痕迹。

    为什么 M2 也要写：
        我们宣称"图片阅后即焚 + 元数据剥离"，但控制台输出不留存、不可审计。
        写进 analysis_run 之后，就能查"何时、处理多少张、剥离多少、销毁多少、残留几"。
        `residual == 0` 就是"销毁成功"的直接证据。

    :return: trace_id（写入失败返回空串，不阻塞主流程）
    """
    from uuid import uuid4

    from hub.persistence.repository import RunRepo

    trace_id = uuid4().hex
    status = "DONE" if stats.residual == 0 and stats.failed == 0 else "DEGRADED"
    payload = {
        "module": "M2",
        # **整包哈希**：同一个包重复上传时用来判重 ✅
        "zip_sha256": zip_sha256,
        "images": stats.images,
        "stripped": stats.stripped,
        "deduped": stats.deduped,
        "vision_calls": stats.images - stats.deduped,
        "failed": stats.failed,
        "destroyed": stats.destroyed,
        "residual": stats.residual,
        "vision_model": vision_model,
        "elapsed_s": stats.elapsed_s,
    }

    try:
        repo = RunRepo()
        repo.create(trace_id, batch_id=None)      # M2 也是跨批次的
        repo.update_status(trace_id, status, stats=payload)
        return trace_id
    except Exception as e:                        # noqa: BLE001 审计失败不该拖垮主流程
        print(f"  [审计] 写 analysis_run 失败：{e}")
        return ""


def _aggregate(image_tags: list[tuple[VisionTags, str, str]]):
    """多图聚合：取最严重的一张 + 各字段取极值。

    - defect_type：严重度最高的那张的类别
    - severity：取最大（一张严重就算整体严重）
    - color_deviation：取最大（某张色差明显就算明显）
    - ocr_text：取第一个非空的
    - confidence：取最严重那张的

    返回 (聚合后的 VisionTags, 最严重图的 phash, 最严重图的 sha256)
    单图时直接返回原值。
    """
    if len(image_tags) == 1:
        return image_tags[0]

    worst = max(image_tags, key=lambda t: (t[0].severity, t[0].color_deviation))
    merged = VisionTags(
        defect_type=worst[0].defect_type,
        severity=max(t[0].severity for t in image_tags),
        color_deviation=max(t[0].color_deviation for t in image_tags),
        ocr_text=next((t[0].ocr_text for t in image_tags if t[0].ocr_text), None),
        confidence=worst[0].confidence,
    )
    return merged, worst[1], worst[2]


def _fetch_rows(batch_id: UUID | None, force: bool):
    """取待处理的评价（有图，且还没生成 review_feature）。"""
    with get_session() as s:
        stmt = (
            select(RecordRow.id, RecordRow.platform_review_id,
                   RecordRow.image_urls, RecordRow.content_clean,
                   RecordRow.batch_id)
            .outerjoin(ReviewFeature, ReviewFeature.review_id == RecordRow.id)
            .where(RecordRow.has_image.is_(True))
        )
        if batch_id is not None:
            stmt = stmt.where(RecordRow.batch_id == batch_id)
        if not force:
            stmt = stmt.where(ReviewFeature.review_id.is_(None))
        return s.execute(stmt).all()


# ══════════════════════════════════════════════════════════════
#  预检（precheck）—— **只对账，不调模型**（2026-11-03 新增）
# ══════════════════════════════════════════════════════════════
def precheck_multimodal(
    image_package: str | Path,
    *,
    batch_id: UUID | None = None,
    allow_loose: bool = False,
    seconds_per_image: float = 8.9,
) -> PrecheckStats:
    """上传前对账：**这个包里的图能对上多少条评价？**

    **为什么必须两步走**
        真实包 428 张 × 8.9 秒 = **约 63 分钟** ✅
        如果先跑再说「只匹配上 1 条」，用户**白等一小时** ✅ ✗
        → 所以先预检（**纯文件列表 + 一次 SQL，秒回** ✅），用户看对账再决定跑不跑

    **成本**：不解压、不写盘、不调模型 ✅
        只做三件事：`zipfile.namelist()` + 一次 `select` + 纯内存匹配

    :param image_package: 图片包（目录或 zip）
    :param batch_id:      只对账这个批次（None = 全部）
    :param allow_loose:   是否允许 L5 宽松匹配（默认 False ✅）
    :param seconds_per_image: 本地视觉模型单张耗时（用于预估 ✅）
    """
    from collections import Counter

    t0 = time.time()
    st = PrecheckStats()

    # ---------- 1. 列包里的文件（不解压 ✅）----------
    pkg = list_package(image_package)
    st.package_files = pkg.count
    st.manifest_source = pkg.manifest_source
    st.manifest_rows = len(pkg.manifest)
    st.name_collisions = len(pkg.dupes)

    # ══════════════════════════════════════════════════════════════
    #  **整包 sha256 + 是否处理过**（2026-11-03 补：上一版只加了字段没填 ✅）
    # ══════════════════════════════════════════════════════════════
    # 判重靠 `pipeline_run.kind`：
    #   异步模式入队时用的是 `kind = f"mm:{sha[:8]}"` ✅
    #   → 存在一条 `mm:<同一个 sha8>` 且状态是 DONE/DEGRADED → **处理过** ✅
    st.zip_sha256 = _package_sha256(image_package)
    if st.zip_sha256:
        from sqlalchemy import select as _sel

        from hub.persistence.db import get_session as _gs
        from hub.persistence.models import PipelineRun as _PR
        _kind = f"mm:{st.zip_sha256[:8]}"
        with _gs() as _s:
            prev = _s.scalars(
                _sel(_PR).where(_PR.kind == _kind)
                .order_by(_PR.created_at.desc()).limit(1)).first()
            if prev is not None:
                st.already_processed = prev.status in ("DONE", "DEGRADED")
                st.prev_trace_id = str(prev.trace_id or "")

    # ---------- 2. 取 DB 里所有「有图」的评价（**force=True 含已处理的** ✅）----------
    rows = _fetch_rows(batch_id, force=True)
    st.reviews_with_image = len(rows)
    if not rows:
        st.est_seconds = round(time.time() - t0, 2)
        return st

    # ---------- 3. 逐条匹配（纯内存 ✅）----------
    used: set[str] = set()
    tier_counter: Counter[str] = Counter()
    unmatched: list[dict] = []
    missing: list[dict] = []

    for row in rows:
        res = resolve_images(
            platform_review_id=row.platform_review_id,
            image_urls=row.image_urls,
            index=pkg,
            allow_loose=allow_loose,
        )
        if not res.files:
            st.reviews_unmatched += 1
            if len(unmatched) < 8:            # **只留 8 条样例**（响应体不能无限大 ✅）
                unmatched.append({
                    "review_id": str(row.platform_review_id),
                    "expected_files": (res.tried or [])[:3],
                })
            continue

        st.reviews_matched += 1
        st.images_matched += len(res.files)
        tier_counter[res.tier] += 1
        used.update(res.files)

        # 这条评价要 N 张、只找到 M 张 → 记样例（**部分缺失也要报** ✅）
        want = len(row.image_urls or [])
        if want > len(res.files) and len(missing) < 6:
            missing.append({
                "review_id": str(row.platform_review_id),
                "expected": want,
                "found": len(res.files),
            })

    # ---------- 4. 包里多余的图（**没被任何评价用到** ✅）----------
    st.images_unused = max(0, pkg.count - len(used))
    st.unused_sample = sorted(set(pkg.files) - used)[:8]

    # ---------- 5. 分级 + 成本 ----------
    st.by_tier = dict(tier_counter)
    if tier_counter:
        main_tier = tier_counter.most_common(1)[0][0]
        st.tier_text = TIER_TEXT.get(main_tier, main_tier)
    st.est_calls = st.images_matched
    st.est_seconds = round(st.images_matched * seconds_per_image, 1)

    # ⚠️ **样例必须回填到 stats**（2026-11-03 修：第一版忘了赋值 ✅）
    st.unmatched_sample = unmatched
    st.missing_sample = missing

    _ = t0
    return st


def _package_sha256(pkg: str | Path) -> str:
    """算图片包（zip）的 sha256 —— **整包幂等判重靠它** ✅

    · 目录 → 返回空串（目录没有"同一个包"的概念 ✅）
    · zip   → 流式计算（471MB 大约 1 秒，可接受 ✅）
    """
    p = Path(pkg)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_assets(assets: list[dict]) -> None:
    """批量写 image_asset（**按 (review_id, sha256) 幂等** ✅）

    为什么用 `(review_id, sha256)` 而不是 `review_id`：
        一条评价有 1~9 张图 ✅ 按 review_id upsert 会**互相覆盖** ✅ ✗
        而同一张图重复处理（force=True）应该**覆盖而不是新增** ✅
    """
    if not assets:
        return
    with get_session() as s:
        upsert_all(s, __import__("hub.persistence.models", fromlist=["ImageAsset"]).ImageAsset,
                   assets, key_cols=["review_id", "sha256"])
        s.commit()


def _write_features(features: list[dict]) -> None:
    """批量写 review_feature（有则更新）。"""
    if not features:
        return
    with get_session() as s:
        upsert_all(s, ReviewFeature, features, key_cols=["review_id"])
        s.commit()
