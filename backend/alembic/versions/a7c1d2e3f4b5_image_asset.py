"""image_asset：图片级明细（P2）

Revision ID: a7c1d2e3f4b5
Revises: e6e7d355e916
Create Date: 2026-11-03 14:00:00.000000

**为什么加这张表**
    `review_feature` 存的是「一条评价合并后的结果」（多图取最严重）
    → 单图信息被丢掉了：
        · 没法回答「第 3 张是破损，另外 2 张正常」
        · 没法做人工复核（复核哪一张？）
        · 没法做模型迭代（单图没有 ground truth）

**这是纯新增**（不动任何已有表 ✅）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a7c1d2e3f4b5'
down_revision: Union[str, None] = 'e6e7d355e916'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'image_asset',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('review_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('batch_id', postgresql.UUID(as_uuid=True), nullable=True),
        # 溯源
        sa.Column('source_file', sa.Text(), nullable=True),
        sa.Column('match_tier', sa.Text(), nullable=True),
        sa.Column('phash_bit', sa.Text(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        # 模型打标（单图）
        sa.Column('defect_type', sa.Text(), nullable=True),
        sa.Column('severity', sa.SmallInteger(), nullable=True),
        sa.Column('color_deviation', sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column('vision_model', sa.Text(), nullable=True),
        sa.Column('degraded', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        # 人工复核
        sa.Column('reviewed_by', sa.Text(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('final_defect', sa.Text(), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['batch_id'], ['ingest_batch.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['review_id'], ['record.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_image_asset_review', 'image_asset', ['review_id'])
    op.create_index('ix_image_asset_phash', 'image_asset', ['phash_bit'])
    op.create_index('ix_image_asset_pending_review', 'image_asset',
                    ['reviewed_at', 'confidence'])


def downgrade() -> None:
    op.drop_index('ix_image_asset_pending_review', table_name='image_asset')
    op.drop_index('ix_image_asset_phash', table_name='image_asset')
    op.drop_index('ix_image_asset_review', table_name='image_asset')
    op.drop_table('image_asset')
