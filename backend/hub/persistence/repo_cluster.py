# -*- coding: utf-8 -*-
"""
问题簇读写（repo_cluster.py）
============================
M4 聚类的数据访问：取差评文本、写 issue_cluster。

注意：`issue_cluster` 是**按 run 隔离**的（run_id 外键），
每次聚类都生成一批新簇，历史保留 —— 这样才能算 growth_rate（环比）。
"""
from uuid import UUID

from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import ClusterMember, IssueCluster
from hub.persistence.models import Record as RecordRow
from hub.persistence.models import ReviewTopic, SpuCatalog


class ClusterRepo:
    """issue_cluster 的读写 + 聚类输入。"""

    def fetch_reviews(self) -> list:
        """取所有差评（已分类的），供聚类使用。

        :return: Row 列表，字段 id / platform / text / topic / category / severity
        """
        with get_session() as s:
            return s.execute(
                select(
                    RecordRow.id,
                    RecordRow.platform,
                    RecordRow.platform_spu_id,
                    RecordRow.content_clean.label("text"),   # 别名：聚类模块按 text 读
                    ReviewTopic.topic,
                    ReviewTopic.severity,
                    SpuCatalog.category_l1.label("category"),
                )
                .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
                .outerjoin(
                    SpuCatalog,
                    SpuCatalog.canonical_spu_id == RecordRow.platform_spu_id,
                )
                # 必须按**业务主键**排序，不能按 record.id（随机 UUID）。
                # 为什么：聚类对输入顺序敏感（KMeans 的 k-means++ 初始化从"第几个点"开始挑），
                # 按随机 UUID 排会导致"同样的数据，每次跑出来的簇都不一样"（实测过）。
                # platform_review_id 形如 TB00000000，是平台侧的业务主键，导入多次都稳定。
                .order_by(RecordRow.platform, RecordRow.platform_review_id)
            ).all()

    def insert_clusters(self, rows: list[dict]) -> tuple[int, int]:
        """批量写 issue_cluster + cluster_member。

        :param rows: 每个 dict 是 IssueCluster 的字段；可带 `_members`（内部字段，
                     该簇全部 record_id 列表），不会被写进 issue_cluster。
        :return: (簇数, 成员数)
        """
        if not rows:
            return 0, 0

        with get_session() as s:
            objs: list[tuple[IssueCluster, list]] = []
            for r in rows:
                # 下划线开头的是内部字段，不是表字段
                data = {k: v for k, v in r.items() if not k.startswith("_")}
                obj = IssueCluster(**data)
                s.add(obj)
                objs.append((obj, r.get("_members") or []))

            s.flush()          # 拿 DB 生成的 id（gen_random_uuid）

            member_rows = []
            for obj, record_ids in objs:
                reps = set(obj.representative_review_ids or [])
                for rid in record_ids:
                    member_rows.append({
                        "cluster_id": obj.id,
                        "record_id": rid,
                        "is_representative": rid in reps,
                    })
            if member_rows:
                s.add_all([ClusterMember(**m) for m in member_rows])

            s.commit()
            return len(rows), len(member_rows)

    def count(self) -> int:
        with get_session() as s:
            return s.scalar(select(func.count()).select_from(IssueCluster)) or 0

    def fetch_latest_clusters(self) -> list:
        """取最近一次 run 的簇（给查看脚本用）。"""
        with get_session() as s:
            latest = s.scalar(
                select(IssueCluster.run_id)
                .order_by(IssueCluster.created_at.desc())
                .limit(1)
            )
            if latest is None:
                return []
            return s.scalars(
                select(IssueCluster)
                .where(IssueCluster.run_id == latest)
                .order_by(IssueCluster.review_count.desc())
            ).all()
