# -*- coding: utf-8 -*-
"""
验证 M2 多模态：跑一次、看落库、验证"阅后即焚"。

运行（在 backend/ 目录下）：
    python tests/check_multimodal.py
"""
import sys
import tempfile
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, select          # noqa: E402

from hub.multimodal.engine import run_multimodal   # noqa: E402
from hub.persistence.db import get_session         # noqa: E402
from hub.persistence.models import ReviewFeature   # noqa: E402

MOCK_DIR = _BACKEND_DIR.parent / "data" / "mock"


def main() -> None:
    # 1. 跑一次
    print("=== 1. 跑多模态处理 ===")
    stats = run_multimodal(MOCK_DIR / "images")
    print(f"待处理 {stats.total}｜成功 {stats.processed}｜跳过 {stats.skipped}")
    print(f"pHash 去重（复用标签）{stats.deduped}")
    print(f"覆写销毁 {stats.destroyed} 个文件")
    print(f"沙箱残留 {stats.residual}（应为 0）")

    # 2. review_feature 落库
    print("\n=== 2. review_feature 落库 ===")
    with get_session() as s:
        n = s.scalar(select(func.count()).select_from(ReviewFeature))
        print(f"总记录 {n}")
        for row in s.execute(
            select(ReviewFeature.defect_type, func.count())
            .group_by(ReviewFeature.defect_type)
            .order_by(func.count().desc())
        ):
            print(f"  {row[0]}: {row[1]}")

    # 3. 抽样一条
    print("\n=== 3. 抽样一条 ===")
    with get_session() as s:
        f = s.scalar(select(ReviewFeature).limit(1))
    if f is not None:
        print(f"defect={f.defect_type} severity={f.severity} "
              f"color_dev={f.color_deviation} conf={f.vision_confidence}")
        print(f"phash={f.phash_bit}  image_sha256={f.image_sha256[:16]}...  "
              f"model={f.vision_model}")

    # 4. 沙箱残留检查（阅后即焚的最终验证）
    print("\n=== 4. 阅后即焚检查 ===")
    leftovers = list(Path(tempfile.gettempdir()).glob("mm_sandbox_*"))
    print(f"遗留沙箱目录 {len(leftovers)} 个（应为 0）")


if __name__ == "__main__":
    main()
