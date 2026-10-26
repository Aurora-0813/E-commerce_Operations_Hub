# -*- coding: utf-8 -*-
"""
查看 M2 的处理结果（抽样 + 分布）。

运行（在 backend/ 目录下）：
    python tests/check_mm_sample.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, select          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402
from hub.persistence.models import ReviewFeature         # noqa: E402


def main() -> None:
    with get_session() as s:
        n_img = s.scalar(
            select(func.count()).select_from(RecordRow)
            .where(RecordRow.has_image.is_(True))
        )
        n_feat = s.scalar(select(func.count()).select_from(ReviewFeature))
        print(f"有图评价 {n_img} 条｜已生成 review_feature {n_feat} 条")

        print("\n=== 缺陷类型分布 ===")
        for row in s.execute(
            select(ReviewFeature.defect_type, func.count())
            .group_by(ReviewFeature.defect_type)
            .order_by(func.count().desc())
        ):
            print(f"  {row[0]}: {row[1]}")

        print("\n=== 严重度分布 ===")
        for row in s.execute(
            select(ReviewFeature.severity, func.count())
            .group_by(ReviewFeature.severity)
            .order_by(ReviewFeature.severity)
        ):
            print(f"  severity={row[0]}: {row[1]}")

        print("\n=== 抽样 10 条 ===")
        for f in s.scalars(select(ReviewFeature).limit(10)):
            print(f"  defect={f.defect_type:6} sev={f.severity} "
                  f"color_dev={f.color_deviation:.2f} conf={f.vision_confidence:.2f} "
                  f"degraded={f.degraded} model={f.vision_model}")


if __name__ == "__main__":
    main()
