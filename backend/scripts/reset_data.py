# -*- coding: utf-8 -*-
"""
清空业务数据（reset_data.py）
=============================
把库里所有业务表清空，让全链可以从零重跑。

**为什么用 `TRUNCATE ... CASCADE` 而不是"按顺序 DELETE"**：

    原来维护了一张手写的删除顺序表。这个做法已经咬过三次：
        ① review_reply_draft.cluster_id → issue_cluster
        ② hitl_task.proposal_id         → action_proposal
        ③ action_outcome.proposal_id    → action_proposal
      每次都是"新增了一张从表，忘了加进列表"，然后 reset 报 FK 违规。

    **手维护依赖顺序是一种会持续产生 bug 的设计**——
    每加一张表都要记得改这里，而"记得"不可靠。

    `TRUNCATE a, b CASCADE` 让**数据库自己**把"引用了这些表的所有表"一起清掉：
        · 不用排序
        · 加新表不用改这里（只要它挂在某张根表下面）
        · 一次事务，比逐表 DELETE 快得多

**根表（roots）怎么选**：只列"别的表指向它、它自己不指向别人"的表。
    ingest_batch   ← record ← review_feature / review_topic
    analysis_run   ← issue_cluster ← cluster_member
                   ← action_proposal ← tool_execution / hitl_task / action_outcome
                   ← anomaly
    spu_catalog / sales_daily / pipeline_run / dead_letter /
    idempotency_record / trace_span / promo_calendar / incremental_cursor
    都是无外键的独立表。

⚠️ 这是开发/测试脚本，**生产绝不能跑**（会清掉全部业务数据）。
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import text                   # noqa: E402

from hub.persistence.db import get_session    # noqa: E402

# 根表：CASCADE 会连带清掉所有引用它们的表
ROOTS = [
    "ingest_batch",         # → record → review_feature / review_topic
    "analysis_run",         # → issue_cluster / action_proposal / anomaly …
    "spu_catalog",
    "sales_daily",
    "pipeline_run",         # M7 调度
    "dead_letter",          # 会跨 reset 累积
    "idempotency_record",   # 幂等窗口是"当天"，reset 后应允许重做
    "trace_span",           # M8 链路追踪
    "promo_calendar",       # M9 大促日历
    "incremental_cursor",
]

# 顺带打印一下这些表，验证 CASCADE 真的把它们清了
CHECK = [
    "record", "review_feature", "review_topic",
    "issue_cluster", "cluster_member",
    "action_proposal", "tool_execution", "hitl_task", "action_outcome",
    "review_reply_draft", "anomaly",
]


def main() -> None:
    with get_session() as s:
        s.execute(text(f"TRUNCATE {', '.join(ROOTS)} CASCADE"))
        s.commit()

        print("清空后各表行数：")
        for t in ROOTS + CHECK:
            n = s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            print(f"  {t:18} {n}")


if __name__ == "__main__":
    main()
