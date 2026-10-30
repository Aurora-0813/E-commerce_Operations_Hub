"""child tables cascade on delete

Revision ID: 06c7018f0414
Revises: c24e6b62e9db
Create Date: 2026-10-30 17:20:57.091295

给三张从表的外键补上 ON DELETE CASCADE：
    action_outcome.proposal_id     → action_proposal.id
    hitl_task.proposal_id          → action_proposal.id
    review_reply_draft.cluster_id  → issue_cluster.id

**为什么要改**：
    这三条外键原来没设 ondelete，导致"删父表"会 FK 违规。
    为此在 reset_data.py 里手写了一串"先删子表"的删除顺序，
    结果新增从表时忘了改 → 连续踩了四次（review_reply_draft / hitl_task /
    action_outcome / seed_history 删 analysis_run）。
    **父没了、子就该没** —— 这是语义上对的做法，不该靠人工维护顺序。

⚠️ 注意本文件是**手改过**的，不是 autogenerate 原样产物：
    Alembic 因为没配 naming convention，把新外键名渲染成 `None`，
    `downgrade()` 里的 `op.drop_constraint(None, ...)` 会直接失败（有 UserWarning 提示）。
    这里改成"drop 旧名 + create 同名"，让 upgrade/downgrade 镜像对称。
    （根治办法是给 MetaData 配 naming_convention，那是个更大的改动，记进债务。）
"""
from typing import Sequence, Union

from alembic import op

revision: str = '06c7018f0414'
down_revision: Union[str, None] = 'c24e6b62e9db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表, 列, 引用表, 引用列, 约束名) —— 三处
_FKS = [
    ("action_outcome", "proposal_id", "action_proposal", "id",
     "action_outcome_proposal_id_fkey"),
    ("hitl_task", "proposal_id", "action_proposal", "id",
     "hitl_task_proposal_id_fkey"),
    ("review_reply_draft", "cluster_id", "issue_cluster", "id",
     "review_reply_draft_cluster_id_fkey"),
]


def upgrade() -> None:
    for table, col, ref_table, ref_col, name in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [col], [ref_col],
                              ondelete="CASCADE")


def downgrade() -> None:
    for table, col, ref_table, ref_col, name in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [col], [ref_col])
