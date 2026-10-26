# -*- coding: utf-8 -*-
"""
验证 DQE 主流程：跑一次、看分布、检查差评保护。

运行（在 backend/ 目录下）：
    python tests/check_engine.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, select          # noqa: E402

from hub.dqe.engine import run_dqe           # noqa: E402
from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402


def main() -> None:
    # 1. 增量跑
    print("=== 1. 跑 DQE（增量）===")
    stats = run_dqe()
    print(f"处理 {stats.total} 条，耗时 {stats.elapsed_s}s，引擎 {stats.engine}")
    print(f"  keep={stats.keep}  review={stats.review}  drop={stats.drop}")
    if stats.total:
        print(f"  降噪率（review+drop）= {(stats.review + stats.drop) / stats.total:.1%}")

    # 2. 再跑一次（增量应该 0 条）
    print("\n=== 2. 再跑一次（增量应为 0 条）===")
    stats2 = run_dqe()
    print(f"处理 {stats2.total} 条（应为 0）")

    # 3. 决策分布
    print("\n=== 3. 库内 decision 分布 ===")
    with get_session() as s:
        for row in s.execute(
            select(RecordRow.dqe_decision, func.count())
            .group_by(RecordRow.dqe_decision)
        ):
            print(f"  {row[0]}: {row[1]}")

    # 4. 差评保护检查（核心！）
    print("\n=== 4. 差评保护检查 ===")
    with get_session() as s:
        n_drop_neg = s.scalar(
            select(func.count()).where(
                RecordRow.rating <= 2, RecordRow.dqe_decision == "drop"
            )
        )
        n_r008 = s.scalar(
            select(func.count()).where(
                RecordRow.rating <= 2,
                RecordRow.dqe_decision == "drop",
                RecordRow.dqe_reason_code == "R008",
            )
        )
    print(f"  rating<=2 且 drop：{n_drop_neg} 条")
    print(f"  其中 R008 注入：{n_r008} 条（允许，攻击必须拦）")
    print(f"  其他差评被 drop：{n_drop_neg - n_r008} 条（应严格为 0）")

    # 5. 原因分布
    print("\n=== 5. reason 分布（看哪个规则抓得多）===")
    with get_session() as s:
        for row in s.execute(
            select(RecordRow.dqe_reason_code, func.count())
            .group_by(RecordRow.dqe_reason_code)
            .order_by(func.count().desc())
        ):
            print(f"  {row[0]}: {row[1]}")


if __name__ == "__main__":
    main()
