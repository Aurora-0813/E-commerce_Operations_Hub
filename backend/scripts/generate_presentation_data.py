# -*- coding: utf-8 -*-
"""
生成一份**新的演示数据**，输出到 `<项目根>/Presentation_materials/`
==================================================================
**和 `scripts/generate_mock.py` 的关系**
    不复制、不改动它 ✅ —— 只在内存里**覆盖几个模块常量**，然后调它的 `main()`
    这样：
        · 原来的 `data/mock/` **一个字节都不动** ✅（全链数字不会作废 ✅）
        · 演示数据独立一套（新 seed → 不同的评价/销量/金额 ✅）

**覆盖了什么**
    · `SEED`      → 新随机种子（默认 20261103）→ 数据完全不同 ✅
    · `DATA_DIR`  → `<根>/Presentation_materials/` ✅
    · 图片包       → **默认不生成**（真实照片一张几百 KB，zip 会到 471MB ✅）
                    要就加 `--with-images`

**产出的文件**（和 `data/mock/` 同名，方便直接替换演示）
    taobao_review_202610.xlsx   淘宝评价
    jd_review_202610.xlsx       京东评价
    taobao_product_202610.xlsx  淘宝商品
    jd_product_202610.xlsx      京东商品
    taobao_sales_202610.xlsx    淘宝销售
    jd_sales_202610.xlsx        京东销售
    gold_labels.csv             自动标注集（内部工作文件）
    historical_interventions.csv 预埋的 6 组干预 + 1 个对照组（M9 的 ground truth）

**⚠️ 日期范围是固定的**（生成器里写死 `datetime(2026, 9, 1)`）
    评价 2026-09-01 ~ 2026-10-22
    销售 2026-07-25 ~ 2026-10-22
    → 前端默认「近 30 天」= 10-04 ~ 11-03，**能看到 10-04~10-22 那段** ✅
    → 想让它"一直有新数据"，改 `generate_mock.py` 的 `gen_reviews()` 里那行 `start` ✅

用法：
    cd backend
    python -B scripts/generate_presentation_data.py
    python -B scripts/generate_presentation_data.py --seed 777 --with-images
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让 `import scripts.generate_mock` 能跑（从 backend 目录执行 ✅）
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import generate_mock as gen                    # noqa: E402

ROOT = BACKEND.parent
DEFAULT_OUT = ROOT / "Presentation_materials"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261103,
                    help="随机种子（和 data/mock 的 42 不同 → 数据完全不同 ✅）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="输出目录（默认 <项目根>/Presentation_materials）")
    ap.add_argument("--images", default="none", choices=("none", "small", "full"),
                    help="图片包：none=不生成 / "
                         "small=**压缩到 640px**（约 20MB，Vision 还认得出内容 ✅）/ "
                         "full=原样复制（约 470MB ✅）")
    ap.add_argument("--max-side", type=int, default=640,
                    help="small 模式的最长边（默认 640 ✅）")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ── 覆盖生成器的三个模块常量 ✅ ──
    gen.SEED = args.seed
    gen.DATA_DIR = out

    if args.images == "none":
        # **跳过图片**（默认 ✅）：两个函数替换成空操作
        # 为什么不直接删 generate_mock 里的调用 —— 那样要改原文件 ✅（不动它 ✅）
        gen.write_images = lambda *a, **kw: 0
        gen.write_images_zip = lambda *a, **kw: None
    elif args.images == "small":
        # **压缩版**（推荐 ✅）：复用真实照片，但缩到 640px + JPEG 85
        #   为什么：原图 1.1MB/张 × 430 张 = 470MB ✅ 太重；
        #          缩到 640px 后约 50KB/张 → **总共 ~20MB** ✅
        #          而 Vision（qwen2.5vl）在 640px 上**完全够用** ✅
        def _small_images(img_dir, reviews, platform, real_dir):
            from PIL import Image as _I

            img_dir.mkdir(parents=True, exist_ok=True)
            real = []
            rd = Path(real_dir) if real_dir else None
            if rd and rd.exists():
                real = sorted(rd.glob("*.jpg")) + sorted(rd.glob("*.png"))
            names: list[str] = []
            for r in reviews:
                names.extend(r.get("image_names") or [])
            if not names:
                return 0
            for i, name in enumerate(names):
                dst = img_dir / name
                if dst.exists():
                    continue
                try:
                    if real:
                        src = real[i % len(real)]
                        with _I.open(src) as im:
                            im = im.convert("RGB")
                            im.thumbnail((args.max_side, args.max_side))
                            im.save(dst, "JPEG", quality=85, optimize=True)
                    else:
                        # 没有真实照片 → 造一张**有色块**的假图（24 位，Vision 能吃 ✅）
                        _I.new("RGB", (args.max_side, args.max_side),
                               (120 + i % 100, 80 + i % 60, 60 + i % 80)
                               ).save(dst, "JPEG", quality=85)
                except Exception as e:      # noqa: BLE001
                    print(f"    [图片] 跳过 {name}：{e}")
            return len(names)

        gen.write_images = _small_images

    print("=" * 66)
    print("  生成演示数据")
    print("=" * 66)
    print(f"  输出目录  {out}")
    print(f"  随机种子  {args.seed}（data/mock 用的是 {42} ✅ 两套完全不同）")
    print(f"  图片包    {args.images}"
          + ("（压缩到 %dpx ✅）" % args.max_side if args.images == "small" else ""))
    print(f"  评价窗口  2026-09-01 ~ 2026-10-22（生成器里写死 ✅）")
    print()

    # ══════════════════════════════════════════════════════════════
    #  ⚠️ **真实照片要「借」过来**（2026-11-03 踩过）
    # ══════════════════════════════════════════════════════════════
    # 生成器里是 `real_dir = DATA_DIR / "images_real"` ✅
    # 而我们把 DATA_DIR 换成了 `Presentation_materials` ✅
    # → 那里没有 images_real → 走「色块假图」分支（6.9KB/张，Vision 认不出内容 ✅）
    # → 所以这里**临时**把 `data/mock/images_real` 复制进来，跑完再删 ✅
    real_src = ROOT / "data" / "mock" / "images_real"
    real_tmp = out / "images_real"
    borrowed = False
    if args.images != "none" and real_src.exists() and not real_tmp.exists():
        import shutil as _sh
        _sh.copytree(real_src, real_tmp)
        borrowed = True
        print(f"  （临时借用真实照片 {len(list(real_tmp.glob('*')))} 张，跑完删除 ✅）")
        print()

    gen.main()

    if borrowed:
        import shutil as _sh2
        _sh2.rmtree(real_tmp, ignore_errors=True)
        print("  （已删除临时借用的 images_real ✅）")

    # ── 汇总产出 ──
    print()
    print("=" * 66)
    print("  产出清单")
    print("=" * 66)
    files = sorted(p for p in out.rglob("*") if p.is_file())
    total = 0
    for f in files:
        size = f.stat().st_size
        total += size
        rel = f.relative_to(out).as_posix()
        print(f"  {rel:<42} {size / 1024:>9.1f} KB")
    print(f"  {'合计':<42} {total / 1024 / 1024:>9.1f} MB")
    print()
    print("  下一步（可选）：把这套导进库里演示")
    print("    • 评价/商品/销售：走「数据接入」页上传，或")
    print(f"      hub p0 \"{out}/taobao_review_202610.xlsx\" --platform taobao --type review")
    print("    • 图片包：默认没生成，需要就加 --with-images ✅")
    print("    ⚠️ 这套数据**和 data/mock 的 platform_id 会重叠**（同一批 SKU）")
    print("       → 入库会**覆盖**同名 platform_id 的记录，不是新增 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
