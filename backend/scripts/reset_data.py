# -*- coding: utf-8 -*-
"""
清空业务数据（保留表结构），用于重新导入。

只删数据、不删表，按外键依赖顺序删：
    先删"依赖别人的"，再删"被依赖的"。

运行（在 backend/ 目录下）：
    python scripts/reset_data.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import text          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402

# 删除顺序（外键依赖：被引用的后删）
#
# ⚠️ 前 4 张表**必须显式删**，不能指望级联：
#     review_reply_draft.cluster_id → issue_cluster（没设 ondelete）
#     hitl_task.proposal_id         → action_proposal（没设 ondelete）
#     这两条外键会在级联删除 issue_cluster / action_proposal 时抛 FK 违规。
#     （"给它们加 ondelete"记进债务：改 schema 要迁移，而现在先保证 reset 能用。）
TABLES = [
    "review_reply_draft",   # 引用 issue_cluster（无级联）
    "hitl_task",            # 引用 action_proposal（无级联）
    "tool_execution",       # 引用 action_proposal（ON DELETE CASCADE，但先删更稳）
    "action_proposal",      # 引用 analysis_run（ON DELETE CASCADE）
    "dead_letter",          # 无外键（但会跨 reset 累积，一并清）
    "idempotency_record",   # 无外键（幂等窗口是"当天"，reset 后应允许重做）
    "trace_span",           # 无外键
    "analysis_run",         # 引用 ingest_batch；级联删 issue_cluster / cluster_member
    "spu_catalog",          # 无外键
    "sales_daily",          # 无外键
    "ingest_batch",         # 被 record 引用（ON DELETE CASCADE 会连带删 record）
]


def main() -> None:
    with get_session() as s:
        for t in TABLES:
            s.execute(text(f"DELETE FROM {t}"))
        s.commit()

        print("清空后各表行数：")
        # record / review_feature / review_topic / issue_cluster / cluster_member
        # 靠 ON DELETE CASCADE 连带删除，这里一并打印，用来验证级联真的生效了
        check = ["record", "review_feature", "review_topic",
                 "issue_cluster", "cluster_member"]
        for t in TABLES + check:
            n = s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            print(f"  {t:18} {n}")


if __name__ == "__main__":
    main()
