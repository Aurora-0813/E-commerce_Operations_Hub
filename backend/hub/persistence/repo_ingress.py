# -*- coding: utf-8 -*-
"""
导入落库（repo_ingress.py）
==========================
把适配器产出的 Record **写进数据库**。

三种数据类型分别落到三张表（各表结构不同，不能硬塞进一张）：
    review  → record       （评价明细，DQE 的直接输入）
    product → spu_catalog  （商品主数据）
    sales   → sales_daily  （日粒度销售聚合）

幂等策略：
    - 同一个文件（tenant + platform + file_sha256）→ 不重复导入，直接返回已有批次
    - 同一条评价（platform + platform_review_id）→ 跳过
    - 商品 / 销售 → 有则更新（upsert）

为什么评论表的 dqe_* 字段写 NULL：
    导入时还没跑 DQE，判定结果不存在。
    dqe_decision / dqe_reason_code / dqe_engine 在模型里已改为可空，
    DQE 跑完后按 review_id 回填。
"""
import hashlib
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hub.contracts import MappingReport, RawBatch, Record
from hub.persistence.db import get_session
from hub.persistence.models import (
    IngestBatch,
    Record as RecordRow,
    SalesDaily,
    SpuCatalog,
)


class IngressRepo:
    """一次导入 = 一个批次 + 若干记录。"""

    def save(self, batch: RawBatch, records: list[Record],
             report: MappingReport) -> tuple[UUID, bool]:
        """落库，返回 (batch_id, 是否已存在)。

        :return: (批次 ID, True 表示该文件之前已导入过、本次跳过)
        """
        with get_session() as s:
            # 1. 幂等：同一个文件只导一次
            existing = s.scalar(select(IngestBatch).where(
                IngestBatch.tenant_id == "default",
                IngestBatch.platform == batch.platform,
                IngestBatch.file_sha256 == batch.file_sha256,
            ))
            if existing is not None:
                return existing.id, True

            # 2. 写批次
            s.add(IngestBatch(
                id=batch.batch_id,
                tenant_id="default",
                platform=batch.platform,
                file_name=batch.file_name,
                file_sha256=batch.file_sha256,
                row_total=report.total,
                row_kept=report.row_ok,
                row_dropped=report.row_bad,
                status="DONE",
            ))
            s.flush()   # 先落批次，保证外键可见

            # 3. 按数据类型分派
            self._save_reviews(s, [r for r in records if r.data_type == "review"],
                               batch.batch_id)
            self._save_products(s, [r for r in records if r.data_type == "product"])
            self._save_sales(s, [r for r in records if r.data_type == "sales"])

            s.commit()
        return batch.batch_id, False

    # ==================== 评价 → record ====================

    def _save_reviews(self, s, records: list[Record], batch_id: UUID) -> None:
        if not records:
            return
        rows = [self._to_record_row(r, batch_id) for r in records]
        stmt = pg_insert(RecordRow).values(rows)
        # 同一条评价（平台 + 平台评价 ID）已存在就跳过，不报错
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["platform", "platform_review_id"]
        )
        s.execute(stmt)

    def _to_record_row(self, r: Record, batch_id: UUID) -> dict:
        """Record 契约 → record 表的一行。"""
        content = r.fields.get("content") or ""
        return {
            "id": UUID(r.record_id),
            "batch_id": batch_id,
            "data_type": "review",
            "platform": r.platform,
            "platform_review_id": r.platform_id,
            "platform_spu_id": r.fields.get("platform_spu_id") or "",
            "canonical_spu_id": r.canonical_spu_id,
            "sku_id": r.fields.get("sku_id"),
            "rating": r.rating,
            "content_clean": content,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "has_image": bool(r.fields.get("has_image")),
            "image_urls": r.fields.get("image_urls") or [],
            "review_time": r.ts,
            "dqe_decision": None,        # 待 DQE 回填
            "dqe_reason_code": None,
            "dqe_engine": None,
            "model_version": None,
        }

    # ==================== 商品 → spu_catalog ====================

    def _save_products(self, s, records: list[Record]) -> None:
        if not records:
            return
        rows = [self._to_spu_row(r) for r in records]
        stmt = pg_insert(SpuCatalog).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["canonical_spu_id"],
            set_={
                "title": stmt.excluded.title,
                "category_l1": stmt.excluded.category_l1,
                "category_l2": stmt.excluded.category_l2,
                "price_ref": stmt.excluded.price_ref,
                "updated_at": func.now(),
            },
        )
        s.execute(stmt)

    def _to_spu_row(self, r: Record) -> dict:
        """Record 契约 → spu_catalog 表的一行。"""
        # 类目形如 "女装/连衣裙"，拆成一二级
        cat = (r.fields.get("category") or "").split("/")
        return {
            # 同款对齐还没做，先拿平台商品 ID 当 canonical_spu_id，对齐后再改
            "canonical_spu_id": r.fields.get("platform_spu_id") or "",
            "title": r.fields.get("title") or "",
            "category_l1": cat[0] if cat and cat[0] else None,
            "category_l2": cat[1] if len(cat) > 1 and cat[1] else None,
            "price_ref": r.monetary.get("price"),
        }

    # ==================== 销售 → sales_daily ====================

    def _save_sales(self, s, records: list[Record]) -> None:
        if not records:
            return
        rows = [self._to_sales_row(r) for r in records]
        stmt = pg_insert(SalesDaily).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["canonical_spu_id", "platform", "stat_date"],
            set_={
                "gmv": stmt.excluded.gmv,
                "units": stmt.excluded.units,
                "uv": stmt.excluded.uv,
                "refund_units": stmt.excluded.refund_units,
                "refund_amount": stmt.excluded.refund_amount,
            },
        )
        s.execute(stmt)

    def _to_sales_row(self, r: Record) -> dict:
        """Record 契约 → sales_daily 表的一行。"""
        return {
            "canonical_spu_id": r.fields.get("platform_spu_id") or "",
            "platform": r.platform,
            "stat_date": date.fromisoformat(r.fields["stat_date"]),
            "gmv": r.monetary.get("gmv") or Decimal("0"),
            "units": r.fields.get("units") or 0,
            "uv": r.fields.get("uv") or 0,
            "refund_units": r.fields.get("refund_units") or 0,
            "refund_amount": r.monetary.get("refund_amount") or Decimal("0"),
            "source": "export",
        }
