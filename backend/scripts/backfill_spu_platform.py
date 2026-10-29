# -*- coding: utf-8 -*-
"""
回填 spu_catalog.platform（backfill_spu_platform.py）
====================================================
新加的 `platform` 列对老数据是 NULL，要从 `record` 推出来：

    record.platform + record.platform_spu_id  →  spu_catalog.platform

为什么可以从 record 推：
    每条评价都带平台和商品 ID，所以"某个商品 ID 属于哪个平台"是可以确定的。
    （商品一定有评价；如果某个商品一条评价都没有，它会被漏掉——真实业务里
     这种情况要用商品导出文件本身带平台列，那是 M11 之后的事。）

运行（在 backend/ 目录下，先跑完 alembic upgrade）：
    python scripts/backfill_spu_platform.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import text          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402


def main() -> None:
    sql = """
    UPDATE spu_catalog c
    SET platform = r.platform
    FROM (SELECT DISTINCT platform, platform_spu_id FROM record) r
    WHERE c.canonical_spu_id = r.platform_spu_id
      AND c.platform IS NULL
    """

    with get_session() as s:
        before = s.execute(text(
            "SELECT platform, count(*) FROM spu_catalog GROUP BY platform ORDER BY platform"
        )).all()
        print("回填前：")
        for p, n in before:
            print(f"  {str(p):<10} {n}")

        result = s.execute(text(sql))
        s.commit()
        print(f"\n✅ 回填 {result.rowcount} 行")

        after = s.execute(text(
            "SELECT platform, count(*) FROM spu_catalog GROUP BY platform ORDER BY platform"
        )).all()
        print("\n回填后：")
        for p, n in after:
            print(f"  {str(p):<10} {n}")

        left = s.execute(text(
            "SELECT count(*) FROM spu_catalog WHERE platform IS NULL"
        )).scalar()
        print(f"\n仍为 NULL：{left} 行（应为 0）")


if __name__ == "__main__":
    main()
