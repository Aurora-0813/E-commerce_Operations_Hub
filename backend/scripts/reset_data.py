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
TABLES = [
    "trace_span",      # 无外键
    "analysis_run",    # 引用 ingest_batch
    "spu_catalog",     # 无外键
    "sales_daily",     # 无外键
    "ingest_batch",    # 被 record 引用（ON DELETE CASCADE 会连带删 record）
]


def main() -> None:
    with get_session() as s:
        for t in TABLES:
            s.execute(text(f"DELETE FROM {t}"))
        s.commit()

        print("清空后各表行数：")
        for t in TABLES + ["record"]:
            n = s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            print(f"  {t:16} {n}")


if __name__ == "__main__":
    main()
