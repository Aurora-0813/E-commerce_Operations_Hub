"""image_asset：加唯一约束 (review_id, sha256)（幂等 upsert 需要）

Revision ID: b8d2e3f4a5c6
Revises: a7c1d2e3f4b5
Create Date: 2026-11-03 14:20:00.000000

**为什么需要**
    `_write_assets()` 用 `INSERT ... ON CONFLICT (review_id, sha256) DO UPDATE`
    → PostgreSQL 要求这个组合**有唯一约束** ✅
    → 第一版只建了普通索引 → 报错
       `InvalidColumnReference: 没有匹配 ON CONFLICT 说明的唯一或者排除约束` ✅

**为什么用 (review_id, sha256) 而不是 review_id**
    一条评价有 1~9 张图 ✅ 按 review_id 唯一会**互相覆盖** ✅ ✗
    而同一张图重复处理（force=True）应该**覆盖而不是新增** ✅
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b8d2e3f4a5c6'
down_revision: Union[str, None] = 'a7c1d2e3f4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 唯一索引（PostgreSQL 的 ON CONFLICT 认唯一索引 ✅）
    op.create_index(
        'uq_image_asset_review_sha', 'image_asset', ['review_id', 'sha256'],
        unique=True,
        postgresql_where=None,
    )


def downgrade() -> None:
    op.drop_index('uq_image_asset_review_sha', table_name='image_asset')
