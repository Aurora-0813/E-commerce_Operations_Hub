# -*- coding: utf-8 -*-
"""
检查图片包和 DB 里的 image_urls 是否一一对应。

用途：排查"沙箱导入 N 张，但只处理了 M 张"（M < N）的问题——
      通常是图片包里残留了上一次的陈旧文件。

运行（在 backend/ 目录下）：
    python tests/check_image_cover.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select          # noqa: E402

from hub.persistence.db import get_session     # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402

IMG_DIR = _BACKEND_DIR.parent / "data" / "mock" / "images"


def main() -> None:
    with get_session() as s:
        rows = s.scalars(
            select(RecordRow.image_urls).where(RecordRow.has_image.is_(True))
        ).all()

    urls = [u for arr in rows for u in (arr or [])]
    names = {Path(u).name for u in urls}
    files = {p.name for p in IMG_DIR.iterdir() if p.is_file()} if IMG_DIR.exists() else set()

    print(f"有图评价        : {len(rows)} 条")
    print(f"图片 URL 总数    : {len(urls)}（去重后 {len(names)}）")
    print(f"图片包文件数    : {len(files)}")
    print(f"URL 有、包里没有 : {len(names - files)}")
    print(f"包里有、URL 没有 : {len(files - names)}  ← 陈旧文件")

    missing = sorted(names - files)
    if missing:
        print("\n缺失的文件（评价引用了但包里没有）：")
        for f in missing[:20]:
            print(f"  {f}")

    stale = sorted(files - names)
    if stale:
        print("\n陈旧的文件（包里有但没人引用）：")
        for f in stale[:30]:
            print(f"  {f}")


if __name__ == "__main__":
    main()
