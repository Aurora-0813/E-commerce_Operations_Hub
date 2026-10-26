# -*- coding: utf-8 -*-
"""
验证导入落库：读文件 → 写库 → 重复导入（幂等）→ 查库统计。

前提：表已建好，且已执行最新迁移（record 的 dqe_* 列可空）。

运行（在 backend/ 目录下）：
    python tests/check_import.py
"""
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, select          # noqa: E402

from hub.ingress.adapter import read_file     # noqa: E402
from hub.persistence.db import get_session    # noqa: E402
from hub.persistence.models import (          # noqa: E402
    IngestBatch,
    Record as RecordRow,
    SalesDaily,
    SpuCatalog,
)
from hub.persistence.repo_ingress import IngressRepo   # noqa: E402

MOCK_DIR = Path(__file__).resolve().parents[2] / "data" / "mock"


def main() -> None:
    repo = IngressRepo()

    # 1. 导入京东评价
    print("=== 1. 导入京东评价 ===")
    batch, records, report = read_file(MOCK_DIR / "jd_review_202610.xlsx", "jd", "review")
    batch_id, existed = repo.save(batch, records, report)
    print(f"批次 {batch_id}｜首次导入={not existed}｜记录 {len(records)} 条")

    # 2. 再导一次同一个文件（应幂等跳过）
    print("\n=== 2. 重复导入同一个文件（应跳过）===")
    batch2, records2, report2 = read_file(MOCK_DIR / "jd_review_202610.xlsx", "jd", "review")
    batch_id2, existed2 = repo.save(batch2, records2, report2)
    print(f"批次 {batch_id2}｜被跳过={existed2}")

    # 3. 库内统计
    print("\n=== 3. 库内统计 ===")
    with get_session() as s:
        n_batch = s.scalar(select(func.count()).select_from(IngestBatch))
        n_rec = s.scalar(select(func.count()).select_from(RecordRow))
        n_spu = s.scalar(select(func.count()).select_from(SpuCatalog))
        n_sales = s.scalar(select(func.count()).select_from(SalesDaily))
    print(f"ingest_batch={n_batch}  record={n_rec}  "
          f"spu_catalog={n_spu}  sales_daily={n_sales}")

    # 4. 抽查一条记录（确认 dqe_* 为 NULL、content_sha256 有值）
    print("\n=== 4. 抽查一条记录 ===")
    with get_session() as s:
        row = s.scalar(select(RecordRow).limit(1))
    if row is not None:
        print(f"platform={row.platform}  rating={row.rating}  "
              f"dqe_decision={row.dqe_decision}")
        print(f"content={row.content_clean[:40]}...")
        print(f"content_sha256={row.content_sha256[:16]}...")


if __name__ == "__main__":
    main()
