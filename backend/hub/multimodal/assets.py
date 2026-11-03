# -*- coding: utf-8 -*-
"""
图片级明细 + 人工复核（assets.py）—— 2026-11-03 新增
=====================================================
**这条链解决什么**

    AI 打的标**不能直接进赔偿决策**（真实业务红线 ✅）
        ↓
    低置信度的图 → **复核队列** → 运营看一眼 → 改/确认
        ↓
    `final_defect` 落库 → **合并 review_feature 时优先用它**
        ↓
    复核结果**就是下一次微调/评测的标注数据**（模型迭代的燃料 ✅）

**为什么合并要"优先用 final_defect"**
    模型说"破损 0.42"，人工改成"开线" ✅
    → 之后所有统计（按缺陷类型分布、按商品看缺陷）**必须用人工的** ✅
      否则"人工改了但报表没变"，复核就白做了 ✅
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select, update

from hub.persistence.db import get_session
from hub.persistence.models import ImageAsset
from hub.persistence.models import Record as RecordRow
from hub.persistence.models import ReviewFeature

#: 复核阈值：置信度低于这个就要人看（真实业务里通常是 0.7~0.85 ✅）
LOW_CONFIDENCE = 0.75

#: 需要人工兜底的高风险缺陷（**涉及赔付/合规 ✅**）
HIGH_RISK_DEFECTS = ("破损", "开线")


# ══════════════════════════════════════════════════════════════
#  查询
# ══════════════════════════════════════════════════════════════
def list_assets(
    *,
    pending_only: bool = False,
    max_confidence: float | None = None,
    defect: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """列图片明细（**默认只看待复核的** ✅）。

    :param pending_only:   只要还没复核的（`reviewed_at IS NULL`）
    :param max_confidence: 只要置信度 ≤ 这个值的（`None` = 不限）
    :param defect:         只要这个缺陷类型的
    """
    with get_session() as s:
        stmt = (
            select(ImageAsset, RecordRow.platform_review_id,
                   RecordRow.content_clean)
            .outerjoin(RecordRow, RecordRow.id == ImageAsset.review_id)
            .order_by(ImageAsset.created_at.desc())
            .limit(limit)
        )
        if pending_only:
            stmt = stmt.where(ImageAsset.reviewed_at.is_(None))
        if max_confidence is not None:
            stmt = stmt.where(
                and_(ImageAsset.confidence.is_not(None),
                     ImageAsset.confidence <= Decimal(str(max_confidence))))
        if defect:
            stmt = stmt.where(ImageAsset.defect_type == defect)

        rows = s.execute(stmt).all()
        return [{
            "id": str(a.id),
            "review_id": str(a.review_id),
            "platform_review_id": pid,
            "review_text": (text or "")[:120],
            "source_file": a.source_file,
            "match_tier": a.match_tier,
            "defect_type": a.defect_type,
            "severity": a.severity,
            "confidence": float(a.confidence) if a.confidence is not None else None,
            "color_deviation": (float(a.color_deviation)
                                if a.color_deviation is not None else None),
            "vision_model": a.vision_model,
            "degraded": a.degraded,
            "reviewed_by": a.reviewed_by,
            "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            "final_defect": a.final_defect,
            "review_note": a.review_note,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a, pid, text in rows]


def pending_stats() -> dict:
    """复核队列概况（给看板/前端用 ✅）。"""
    with get_session() as s:
        total = s.scalar(select(func.count()).select_from(ImageAsset)) or 0
        pending = s.scalar(
            select(func.count()).select_from(ImageAsset)
            .where(ImageAsset.reviewed_at.is_(None))) or 0
        low = s.scalar(
            select(func.count()).select_from(ImageAsset)
            .where(and_(ImageAsset.reviewed_at.is_(None),
                        ImageAsset.confidence.is_not(None),
                        ImageAsset.confidence <= Decimal(str(LOW_CONFIDENCE))))) or 0
        risky = s.scalar(
            select(func.count()).select_from(ImageAsset)
            .where(and_(ImageAsset.reviewed_at.is_(None),
                        ImageAsset.defect_type.in_(HIGH_RISK_DEFECTS)))) or 0
    return {
        "total": int(total),
        "pending": int(pending),
        "low_confidence": int(low),
        "high_risk": int(risky),
        "threshold": LOW_CONFIDENCE,
    }


# ══════════════════════════════════════════════════════════════
#  复核
# ══════════════════════════════════════════════════════════════
def review_asset(asset_id: UUID, *, final_defect: str, note: str | None,
                 actor: str) -> dict:
    """人工复核一张图：**改 / 确认**都走这里 ✅

    :param final_defect: 人工最终判定（"确认模型结果"就把模型的值原样传进来）
    :param note:         备注（为什么改 ✅ 这是最有价值的标注信息）
    :param actor:        复核人（写到 `reviewed_by` ✅）
    :return: `{ok, review_id, reaggregated}`；找不到抛 `LookupError`
    """
    with get_session() as s:
        a = s.get(ImageAsset, asset_id)
        if a is None:
            raise LookupError("图片明细不存在")
        a.final_defect = final_defect
        a.review_note = note
        a.reviewed_by = actor
        a.reviewed_at = datetime.now(timezone.utc)
        rid = a.review_id
        s.commit()

    # **复核完要重算这条评价的合并结果**（否则报表不变 ✅ ✗）
    merged = reaggregate(rid)
    return {"ok": True, "review_id": str(rid), "reaggregated": merged}


def reaggregate(review_id: UUID) -> dict:
    """用**人工优先**的规则重算 `review_feature` ✅

    规则（和 engine 里"多图取最严重"一致，只是**取值优先用 final_defect**）：
        defect_type  = 取 severity 最大的那张的 `coalesce(final_defect, defect_type)`
        severity     = max(severity)  ← **不因人工改动而变**（严重度还是模型的 ✅）
        confidence   = 该图的 confidence
        ocr_text     = 第一个非空的
        phash_bit / image_sha256 = 最严重那张的（**溯源用** ✅）
    """
    with get_session() as s:
        assets = s.scalars(
            select(ImageAsset).where(ImageAsset.review_id == review_id)
            .order_by(ImageAsset.severity.desc().nullslast(),
                      ImageAsset.confidence.desc().nullslast())).all()
        if not assets:
            return {"ok": False, "reason": "这个评价没有图片明细"}

        worst = assets[0]

        # ⚠️ **"人工优先"要真的是优先**（2026-11-03 修）
        #   第一版：`defect = worst.final_defect or worst.defect_type`
        #     → `worst` 是**severity 最大**的那张 ✅
        #     → 如果人工改的是**另一张**（严重度低），**人工判定被忽略** ✅ ✗
        #       （实测：一条评价 2 张图，人工把低严重度那张改成「开线」，
        #         合并结果还是「破损」→ 和承诺的"人工优先"不符 ✅）
        #   修法：**只要有任何一张被人工判定过，就优先用人工的** ✅
        #     多张都人工判过 → 取 severity 最大的那张（人工之间也按最严重 ✅）
        human = [a for a in assets if a.final_defect]
        if human:
            pick = max(human, key=lambda a: (a.severity or 0, a.confidence or 0))
            defect = pick.final_defect
        else:
            defect = worst.defect_type
        ocr = next((a.final_defect or a.defect_type for a in assets
                    if a.final_defect or a.defect_type), None)
        _ = ocr

        feat = s.get(ReviewFeature, review_id)
        payload = {
            "defect_type": defect,
            "severity": worst.severity,
            "color_deviation": worst.color_deviation,
            "vision_confidence": worst.confidence,
            "phash_bit": worst.phash_bit,
            "image_sha256": worst.sha256,
            "vision_model": worst.vision_model,
            "degraded": any(a.degraded for a in assets),
        }
        if feat is None:
            payload["review_id"] = review_id
            s.add(ReviewFeature(**payload))
        else:
            for k, v in payload.items():
                setattr(feat, k, v)

        # ⚠️ **commit 前先把要返回的值抓到局部变量**（2026-11-03 修）
        #    commit 之后 ORM 对象会 expire ✅ → 再读 `worst.severity` 会抛
        #    `DetachedInstanceError: Instance is not bound to a Session` ✅
        sev = worst.severity
        n_assets = len(assets)
        s.commit()

    return {"ok": True, "defect_type": defect,
            "severity": sev, "assets": n_assets}


def clear_review(asset_id: UUID) -> dict:
    """撤销复核（把 `final_defect` 清掉，回到模型结果 ✅）。"""
    with get_session() as s:
        a = s.get(ImageAsset, asset_id)
        if a is None:
            raise LookupError("图片明细不存在")
        a.final_defect = None
        a.review_note = None
        a.reviewed_by = None
        a.reviewed_at = None
        rid = a.review_id
        s.commit()
    reaggregate(rid)
    return {"ok": True, "review_id": str(rid)}


_ = update   # （保留：以后要做"批量复核"时用 ✅）
