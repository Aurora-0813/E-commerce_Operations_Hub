# -*- coding: utf-8 -*-
"""
验证同款对齐：读 spu_catalog → 跑 align_products → 看对不对。

预期（我们的 mock 数据）：
    淘宝 25 个、京东 25 个
    自动匹配 20 对（两平台标题完全相同 → 相似度 1.0）
    淘宝独占 5 个、京东独占 5 个
    canonical 总数 30 个
    人工候选 0 对

运行（在 backend/ 目录下，先跑完 alembic + 回填）：
    python tests/check_align.py
"""
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.analysis.align import align_products, normalize_title   # noqa: E402
from hub.persistence.repo_sku import SkuRepo                     # noqa: E402


def main() -> None:
    repo = SkuRepo()
    catalog = repo.fetch_catalog()
    if not catalog:
        print("❌ spu_catalog 是空的，或 platform 全是 NULL（先跑 backfill_spu_platform.py）")
        return

    tb = [{"spu_id": r.canonical_spu_id, "title": r.title}
          for r in catalog if r.platform == "taobao"]
    jd = [{"spu_id": r.canonical_spu_id, "title": r.title}
          for r in catalog if r.platform == "jd"]

    print(f"目录：淘宝 {len(tb)} 个｜京东 {len(jd)} 个\n")

    # 先看一眼归一化效果
    print("归一化示例：")
    for p in tb[:2]:
        print(f"  {p['title']!r}")
        print(f"    → {normalize_title(p['title'])!r}")
    print()

    result = align_products(tb, jd)

    print("=== 对齐结果 ===")
    print(f"  自动匹配    {result.matched_pairs} 对（预期 20）")
    print(f"  淘宝独占    {result.tb_only} 个（预期 5）")
    print(f"  京东独占    {result.jd_only} 个（预期 5）")
    print(f"  canonical   {result.canonical_total} 个（预期 30）")
    print(f"  人工候选    {len(result.review_pairs)} 对（预期 0）")

    # 每个 canonical 应该挂 1 或 2 个平台商品
    per_canonical = Counter(m["canonical_spu_id"] for m in result.mappings)
    dist = Counter(per_canonical.values())
    print(f"\n  每个 canonical 挂几个平台商品：{dict(dist)}"
          f"（预期 {{1: 10, 2: 20}}）")

    print("\n=== 前 6 条映射 ===")
    for m in result.mappings[:6]:
        print(f"  {m['canonical_spu_id']}  {m['platform']:<7} "
              f"{m['platform_spu_id']:<8} {m['source']:<6} conf={m['confidence']}")

    # 校验
    ok = (result.matched_pairs == 20 and result.tb_only == 5
          and result.jd_only == 5 and result.canonical_total == 30)

    # 库里已经写进去多少（跑过 hub align 才有）
    print(f"\n库里已有 sku_mapping：{repo.count()} 行（跑了 hub align 后应为 50）")
    print("\n" + ("✅ 对齐结果符合预期" if ok else "⚠️ 结果和预期不符，检查数据或阈值"))


if __name__ == "__main__":
    main()
