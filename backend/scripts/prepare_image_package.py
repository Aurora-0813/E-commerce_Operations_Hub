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
import zipfile
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


def _make_synthetic_images(count: int = 50) -> int:
    """`images_real/` 不存在时，生成一批合成图兜底。

    为什么要兜底：
        `images_real/`（50 张 AI 生成的商品图）体积大，没有进 git。
        别人 clone 下来跑 `run_all.py`，第 [4] 步就会因为目录不存在而中断。

    为什么每张颜色不同：
        M2 用 pHash 去重。如果 50 张图完全一样，会全被去重成 1 张，
        真实调用次数从 50 掉到 1，指标全乱。
    """
    from PIL import Image

    REAL_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        rgb = (25 * (i % 10) + 5, 30 * (i % 8) + 5, 20 * (i % 6) + 5)
        Image.new("RGB", (80, 80), rgb).save(REAL_DIR / f"{i:02d}_synthetic.png")
    return count


def main() -> None:
    if not REAL_DIR.exists() or not any(
            p.suffix.lower() in IMAGE_EXTS for p in REAL_DIR.iterdir()):
        print(f"⚠️ 真实图片目录不存在或为空：{REAL_DIR}")
        n = _make_synthetic_images()
        print(f"   已生成 {n} 张合成图兜底（M2 的结果会和真实图不同，但链路能跑通）")
        print("   想要真实图：见 docs/开发指南/05_M2_多模态沙箱.md 里的生成提示词")

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

    # 先清掉上一次留下的旧图片。
    # 不清的话，包里会出现"没人引用的陈旧文件"，导致
    # 沙箱导入数 > 实际处理数（看起来像 bug）。
    stale = 0
    for p in OUT_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            p.unlink()
            stale += 1
    if stale:
        print(f"清理旧图片 {stale} 张")

    for i, name in enumerate(names):
        shutil.copy2(real_images[i % len(real_images)], OUT_DIR / name)

    # 同步重建 zip：目录和 zip 必须一致，
    # 否则用 images.zip 跑 M2 会又导入那些陈旧文件。
    zip_path = OUT_DIR.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.write(OUT_DIR / name, arcname=name)

    print(f"✅ 图片包已准备：{len(names)} 个文件（循环使用 {len(real_images)} 张真实图）")
    print(f"   输出目录：{OUT_DIR}")
    print(f"   同步 zip：{zip_path}（{len(names)} 个文件）")


if __name__ == "__main__":
    main()
