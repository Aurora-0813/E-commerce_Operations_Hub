# -*- coding: utf-8 -*-
"""
价格背离的数据访问（repo_price.py）
==================================
需要三样东西拼在一起：
    ① `sku_mapping`   —— 把两平台的商品 ID 映射到同一个 canonical
    ② `spu_catalog`   —— 每个平台商品的价格（price_ref）
    ③ `record`        —— 每个商品的评价数 / 差评数（算差评率）
"""
from sqlalchemy import and_, case, func, select

from hub.persistence.db import get_session
from hub.persistence.models import Anomaly, SkuMapping
from hub.persistence.models import Record as RecordRow
from hub.persistence.models import SpuCatalog


class PriceRepo:
    """价格背离相关的读写。"""

    def fetch_stats(self) -> list:
        """每个 (平台, 商品) 的价格 + 评价数 + 差评数。

        :return: Row 列表，字段 platform / spu_id / title / price_ref / reviews / bad
        """
        with get_session() as s:
            stmt = (
                select(
                    SpuCatalog.platform,
                    SpuCatalog.canonical_spu_id.label("spu_id"),
                    SpuCatalog.title,
                    SpuCatalog.price_ref,
                    func.count(RecordRow.id).label("reviews"),
                    func.sum(case((RecordRow.rating <= 2, 1), else_=0)).label("bad"),
                )
                .outerjoin(
                    RecordRow,
                    and_(RecordRow.platform == SpuCatalog.platform,
                         RecordRow.platform_spu_id == SpuCatalog.canonical_spu_id),
                )
                .where(SpuCatalog.platform.isnot(None))
                .group_by(SpuCatalog.platform, SpuCatalog.canonical_spu_id,
                          SpuCatalog.title, SpuCatalog.price_ref)
            )
            return s.execute(stmt).all()

    def fetch_sku_map(self) -> dict[tuple[str, str], str]:
        """{(platform, platform_spu_id): canonical_spu_id}"""
        with get_session() as s:
            rows = s.execute(
                select(SkuMapping.platform, SkuMapping.platform_spu_id,
                       SkuMapping.canonical_spu_id)
            ).all()
        return {(r.platform, r.platform_spu_id): r.canonical_spu_id for r in rows}

    def insert_anomalies(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        with get_session() as s:
            s.add_all([Anomaly(**r) for r in rows])
            s.commit()
        return len(rows)

    def count(self) -> int:
        with get_session() as s:
            return s.scalar(
                select(func.count()).select_from(Anomaly)
                .where(Anomaly.type == "price")
            ) or 0
