# -*- coding: utf-8 -*-
"""
同款映射读写（repo_sku.py）
==========================
`sku_mapping` 表的读写 + 商品目录的读取。

表关系：
    spu_catalog（平台商品，50 行）   ← 现在 PK 存的是平台商品 ID
    sku_mapping（同款映射，50 行）    ← (platform, platform_spu_id) → canonical_spu_id
"""
from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import SkuMapping, SpuCatalog
from hub.persistence.upsert import upsert_all


class SkuRepo:
    """sku_mapping 的读写。"""

    def fetch_catalog(self) -> list:
        """取商品目录（带平台），供对齐算法使用。

        :return: Row 列表，字段 canonical_spu_id（实为平台商品 ID）/ platform / title / category_l1 / price_ref
        """
        with get_session() as s:
            return s.execute(
                select(
                    SpuCatalog.canonical_spu_id,
                    SpuCatalog.platform,
                    SpuCatalog.title,
                    SpuCatalog.category_l1,
                    SpuCatalog.price_ref,
                )
                .where(SpuCatalog.platform.isnot(None))   # 没平台的先跳过（等回填）
                .order_by(SpuCatalog.canonical_spu_id)
            ).all()

    def upsert_many(self, rows: list[dict]) -> int:
        """批量写 sku_mapping（(platform, platform_spu_id) 冲突则更新）。

        :param rows: [{"canonical_spu_id", "platform", "platform_spu_id", "source", "confidence"}]
        """
        if not rows:
            return 0
        with get_session() as s:
            upsert_all(s, SkuMapping, rows, key_cols=["platform", "platform_spu_id"])
            s.commit()
        return len(rows)

    def count(self) -> int:
        with get_session() as s:
            return s.scalar(select(func.count()).select_from(SkuMapping)) or 0

    def fetch_map(self) -> dict[tuple[str, str], str]:
        """{(platform, platform_spu_id): canonical_spu_id}——M4 做跨平台聚合时用。"""
        with get_session() as s:
            rows = s.execute(
                select(SkuMapping.platform, SkuMapping.platform_spu_id,
                       SkuMapping.canonical_spu_id)
            ).all()
        return {(r.platform, r.platform_spu_id): r.canonical_spu_id for r in rows}
