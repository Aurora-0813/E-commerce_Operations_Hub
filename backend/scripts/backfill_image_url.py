# -*- coding: utf-8 -*-
"""
回填 image_url（backfill_image_url.py）
=====================================
record 表新增了 image_url 列（M2 要用），但已有数据是 NULL，需要从导出文件回填。

只更新有图的评价（image_url 非空）。

运行（在 backend/ 目录下，先跑完 alembic upgrade head）：
    python scripts/backfill_image_url.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import bindparam, update          # noqa: E402

from hub.ingress.adapter import read_file          # noqa: E402
from hub.persistence.db import get_session         # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402

MOCK_DIR = _BACKEND_DIR.parent / "data" / "mock"

FILES = [
    ("taobao_review_202610.xlsx", "taobao"),
    ("jd_review_202610.xlsx", "jd"),
]


def backfill(platform: str, filename: str) -> int:
    """读文件 → 取有图的记录 → 更新 record.image_url。"""
    path = MOCK_DIR / filename
    if not path.exists():
        print(f"  跳过（文件不存在）：{path}")
        return 0

    _, records, _ = read_file(path, platform, "review")
    params = [
        {"rid": r.platform_id, "urls": r.fields["image_urls"]}
        for r in records
        if r.fields.get("image_urls")
    ]
    if not params:
        return 0

    stmt = (
        update(RecordRow.__table__)
        .where(RecordRow.platform_review_id == bindparam("rid"))
        .values(image_urls=bindparam("urls"))
    )
    with get_session() as s:
        s.connection().execute(stmt, params)
        s.commit()
    return len(params)


def main() -> None:
    total = 0
    for filename, platform in FILES:
        n = backfill(platform, filename)
        print(f"  {platform}: 回填 {n} 条 image_urls")
        total += n
    print(f"完成，共回填 {total} 条")


if __name__ == "__main__":
    main()
