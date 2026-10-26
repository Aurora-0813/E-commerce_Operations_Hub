# -*- coding: utf-8 -*-
"""
查库计数：确认数据到底有没有写进数据库。

运行（在 backend/ 目录下）：
    python tests/check_counts.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import text             # noqa: E402

from hub.core.config import Settings    # noqa: E402
from hub.persistence.db import get_session   # noqa: E402


def main() -> None:
    s = Settings()
    print("当前连接的数据库：")
    print(f"  {s.storage.url}\n")
    print("（请和 Navicat 里连接的库名对一下，是否一致）\n")

    tables = ("ingest_batch", "record", "spu_catalog", "sales_daily",
              "analysis_run", "trace_span")
    with get_session() as sess:
        print("=== 各表行数 ===")
        for t in tables:
            n = sess.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            print(f"  {t:16} {n}")

        print("\n=== record 按平台 ===")
        for row in sess.execute(text(
            "SELECT platform, count(*) FROM record GROUP BY platform ORDER BY platform"
        )):
            print(f"  {row[0]}: {row[1]}")

        print("\n=== record 按 dqe_decision ===")
        for row in sess.execute(text(
            "SELECT dqe_decision, count(*) FROM record GROUP BY dqe_decision"
        )):
            print(f"  {row[0]}: {row[1]}")

        print("\n=== ingest_batch 明细 ===")
        for row in sess.execute(text(
            "SELECT platform, file_name, row_kept, status, created_at "
            "FROM ingest_batch ORDER BY created_at DESC LIMIT 5"
        )):
            print(f"  {row[0]} | {row[1]} | kept={row[2]} | {row[3]} | {row[4]}")


if __name__ == "__main__":
    main()
