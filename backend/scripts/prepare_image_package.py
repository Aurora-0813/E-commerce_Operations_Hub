# -*- coding: utf-8 -*-
"""
准备图片包（prepare_image_package.py）
====================================
把 data/mock/images_real/ 里的真实图片，按 record.image_url 的 basename
复制进图片包（data/mock/images/）。

为什么需要这步：
    评价里的 image_url 是假 CDN 名（如 O1CN01xxxxx.jpg / 00007.jpg），
    真实图片叫 normal_001.jpg。M2 用 URL 的 basename 找文件，
    所以要把真实图片"改名"成那些 basename。

前提：先跑完 alembic upgrade head + backfill_image_url.py（record.image_url 有值）。

运行（在 backend/ 目录下）：
    python scripts/prepare_image_package.py
"""
import shutil
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402

MOCK_DIR = _BACKEND_DIR.parent / "data" / "mock"
REAL_DIR = MOCK_DIR / "images_real"
OUT_DIR = MOCK_DIR / "images"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    if not REAL_DIR.exists():
        print(f"❌ 真实图片目录不存在：{REAL_DIR}")
        return

    real_images = sorted(p for p in REAL_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not real_images:
        print(f"❌ {REAL_DIR} 里没有图片")
        return

    # 取所有 distinct 的图片 basename（一条评价可能有多张图）
    with get_session() as s:
        rows = s.scalars(
            select(RecordRow.image_urls).where(RecordRow.image_urls.is_not(None))
        ).all()
    names = sorted({Path(u).name for arr in rows for u in (arr or []) if u})

    if not names:
        print("❌ record.image_urls 全是空，先跑 scripts/backfill_image_url.py")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        shutil.copy2(real_images[i % len(real_images)], OUT_DIR / name)

    print(f"✅ 图片包已准备：{len(names)} 个文件（循环使用 {len(real_images)} 张真实图）")
    print(f"   输出目录：{OUT_DIR}")


if __name__ == "__main__":
    main()
