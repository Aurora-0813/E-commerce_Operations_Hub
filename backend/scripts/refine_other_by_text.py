# -*- coding: utf-8 -*-
"""
回填"其他"类别（refine_other_by_text.py）
========================================
把 review_feature 里 defect_type='其他' 的行，用评价文字补成具体类别。

为什么要这个脚本：
    之前跑 M2 时，图片判成"其他"的 4 条已经有了 review_feature。
    现在加了"文字补类别"逻辑，不必重新跑 Vision（省 4 分钟），
    直接对着已有结果改就行。

运行（在 backend/ 目录下）：
    python scripts/refine_other_by_text.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import bindparam, select, update   # noqa: E402

from hub.multimodal.text_hint import infer_defect_from_text   # noqa: E402
from hub.persistence.db import get_session         # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402
from hub.persistence.models import ReviewFeature         # noqa: E402


def main() -> None:
    with get_session() as s:
        rows = s.execute(
            select(ReviewFeature.review_id, RecordRow.content_clean)
            .join(RecordRow, RecordRow.id == ReviewFeature.review_id)
            .where(ReviewFeature.defect_type == "其他")
        ).all()

    if not rows:
        print("没有 defect_type='其他' 的记录，无需回填")
        return

    print(f"发现 {len(rows)} 条'其他'，按文字匹配：")
    updates = []
    for review_id, text in rows:
        hinted = infer_defect_from_text(text)
        snippet = (text or "")[:30]
        if hinted:
            updates.append({"rid": review_id, "cat": hinted})
            print(f"  ✅ {hinted:<4} ← {snippet}")
        else:
            print(f"  ⏭  保持其他 ← {snippet}")

    if not updates:
        print("\n没有可回填的记录")
        return

    tbl = ReviewFeature.__table__
    stmt = (
        update(tbl)
        .where(tbl.c.review_id == bindparam("rid"))
        .values(defect_type=bindparam("cat"))
    )
    with get_session() as s:
        s.connection().execute(stmt, updates)
        s.commit()

    print(f"\n✅ 已回填 {len(updates)} 条")


if __name__ == "__main__":
    main()
