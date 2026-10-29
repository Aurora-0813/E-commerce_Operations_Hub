# -*- coding: utf-8 -*-
"""
通用 upsert（upsert.py）
=======================
**所有** `INSERT ... ON CONFLICT DO UPDATE` 都走这个函数，不要手写 `set_`。

为什么必须统一：
    手写 `set_` 漏一个字段**不报错**——冲突时那一列保持旧值，
    只有查库才发现（实测：`review_topic.secondary_topics` 漏了，
    `--force` 重跑后多标签永远是空数组，但 CLI 统计显示 163 条）。

    `set_` 从 `rows[0]` 的 key 自动生成，以后加字段不用改这里。
"""
from sqlalchemy.dialects.postgresql import insert as pg_insert


def upsert_all(session, model, rows: list[dict], key_cols: list[str],
               extra_set: dict | None = None) -> int:
    """批量 upsert：冲突时更新 `rows` 里**除主键外的所有字段**。

    :param session:  SQLAlchemy Session
    :param model:    目标表模型
    :param rows:     [{"col": value, ...}, ...]（每行的 key 必须一致）
    :param key_cols: 冲突键（主键列名列表）
    :param extra_set: 额外要更新的列（用于 `updated_at=func.now()` 这类
                       **不在 rows 里的服务端表达式**）
    :return: 写入行数
    """
    if not rows:
        return 0

    # 一致性检查：所有行的 key 必须相同，否则 values() 会失败
    keys = set(rows[0])
    for i, row in enumerate(rows):
        if set(row) != keys:
            raise ValueError(
                f"upsert_all: 第 {i} 行的字段和第一行不一致"
                f"（多出 {set(row) - keys}，缺少 {keys - set(row)}）"
            )

    stmt = pg_insert(model).values(rows)

    # 自动生成 set_：除主键外的所有字段
    update_cols = [k for k in rows[0] if k not in key_cols]
    set_ = {k: getattr(stmt.excluded, k) for k in update_cols}
    if extra_set:
        set_.update(extra_set)

    if set_:
        stmt = stmt.on_conflict_do_update(index_elements=key_cols, set_=set_)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=key_cols)

    session.execute(stmt)
    return len(rows)
