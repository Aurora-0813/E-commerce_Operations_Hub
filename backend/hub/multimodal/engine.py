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
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from hub.contracts import VisionTags
from hub.core.tracing import traced
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
) -> MultimodalStats:
    """跑一次多模态处理。

    :param image_package: 图片包路径（目录或 zip）
    :param batch_id:      只处理这个批次（None = 全部）
    :param force:         True = 重处理（覆盖已有 review_feature）
    :param vision:        自定义 Vision（默认 MockVision）
    :param limit:         只处理前 N 条（本地模型慢，先抽检用）
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
    features: list[dict] = []            # 待批量写库

    with Sandbox() as sb:
        imported = sb.ingest(image_package)
        print(f"沙箱导入 {imported} 张图片，待处理 {len(rows)} 条评价")

        for row in rows:
            # 1. 没图 → 跳过
            if not row.image_urls:
                stats.skipped += 1
                continue

            # 2. 逐张处理（一条评价最多 9 张图）
            image_tags: list[tuple[VisionTags, str, str]] = []   # (tags, phash, sha256)
            degraded = False

            for url in row.image_urls:
                path = sb.find(Path(url).name)
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

            if not image_tags:
                stats.skipped += 1    # 所有图都不在包里
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

    # with 退出 → 沙箱覆写销毁
    stats.destroyed = sb.destroyed
    stats.residual = sb.residual()
    stats.stripped = sb.stripped

    # 批量写 review_feature
    _write_features(features)

    stats.elapsed_s = round(time.time() - t0, 2)

    # 写运行记录（审计）：没有这一条，我们只是在"声称合规"，不是"证明合规"
    stats.trace_id = _write_run(stats, v.name)

    return stats


def _write_run(stats: "MultimodalStats", vision_model: str) -> str:
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
                   RecordRow.image_urls, RecordRow.content_clean)
            .outerjoin(ReviewFeature, ReviewFeature.review_id == RecordRow.id)
            .where(RecordRow.has_image.is_(True))
        )
        if batch_id is not None:
            stmt = stmt.where(RecordRow.batch_id == batch_id)
        if not force:
            stmt = stmt.where(ReviewFeature.review_id.is_(None))
        return s.execute(stmt).all()


def _write_features(features: list[dict]) -> None:
    """批量写 review_feature（有则更新）。"""
    if not features:
        return
    with get_session() as s:
        upsert_all(s, ReviewFeature, features, key_cols=["review_id"])
        s.commit()
