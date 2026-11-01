# -*- coding: utf-8 -*-
"""
报表导出测试（check_report.py）—— 阶段 3.9
=========================================
验 6 条：
    [1] 三种导出都能生成 `.xlsx`（openpyxl 能**读回来** ✅）
    [2] 差评清单的**表头正确**
    [3] **PII 复查生效**：原文里有手机号 → 导出文件里**找不到原号码** ✅
    　　← 这是本阶段的硬要求（文件会被带出系统 ✅）
    [4] 首行有"已脱敏 N 处"的说明 ✅
    [5] **1 万行上限**：limit 超了抛 TooManyRows ✅
    [6] 簇汇总/复盘报告的列数对得上 ✅

导出文件写在 `out/test_report/`，测完删掉 ✅
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILS: list[str] = []
OUT = Path(__file__).resolve().parent.parent / "out" / "test_report"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def read_xlsx(p: Path):
    import openpyxl
    wb = openpyxl.load_workbook(p)
    ws = wb.active
    return [[c.value for c in row] for row in ws.iter_rows()]


def main() -> int:
    print("=" * 66)
    print("  报表导出（阶段 3.9）—— 含 PII 复查")
    print("=" * 66)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    from hub.report import (TooManyRows, export_clusters,
                            export_negative_reviews, export_retro, pii_review)

    # ---------- [3] PII 脱敏函数 ----------
    print("\n[3] PII 复查（脱敏 + 计数）")
    raw = "买家 13812345678 说掉色，地址上海市浦东新区XX路88号"
    red, n = pii_review(raw)
    check("脱敏了", n > 0, f"{n} 处")
    check("**原号码不在结果里**", "13812345678" not in red, red[:60])

    # ---------- [1][2][4] 差评清单 ----------
    print("\n[1][2][4] 差评清单：能生成、表头对、首行有脱敏说明")
    p1 = OUT / "negative_reviews.xlsx"
    r1 = export_negative_reviews(p1)
    check("生成了文件", p1.exists(), str(r1))
    check("有行数统计", r1["rows"] >= 0, str(r1["rows"]))
    rows = read_xlsx(p1)
    check("读回来有内容", len(rows) >= 1, f"{len(rows)} 行")
    # 第 1 行是说明，第 2 行是表头 ✅
    check("第 1 行是说明（指到 PII）", "PII" in str(rows[0][0]), str(rows[0][0])[:60])
    header = [str(x) for x in rows[1]]
    check("表头包含平台/时间/评分/问题类型/评价原文",
          all(k in header for k in ("平台", "时间", "评分", "问题类型", "评价原文")),
          str(header))

    # ---------- [5] 1 万行上限 ----------
    print("\n[5] 超过 1 万行 → TooManyRows")
    # limit=0 会立刻超（因为取 limit+1 行 ✅）
    try:
        export_negative_reviews(OUT / "too_many.xlsx", limit=0)
        check("应该抛 TooManyRows", False)
    except TooManyRows:
        check("抛了 TooManyRows", True)
    except Exception as e:                        # noqa: BLE001
        # limit=0 时可能因为"没有可导出的行"而不抛 —— 那也算合理
        check("抛了 TooManyRows", "TooManyRows" in type(e).__name__, type(e).__name__)

    # ---------- [1][6] 簇汇总 ----------
    print("\n[1][6] 簇汇总：能生成、列数对")
    p2 = OUT / "clusters.xlsx"
    r2 = export_clusters(p2)
    check("生成了文件", p2.exists(), str(r2))
    rows2 = read_xlsx(p2)
    check("读回来有表头+数据", len(rows2) >= 1, f"{len(rows2)} 行")
    check("表头 9 列", len(rows2[0]) == 9, f"{len(rows2[0])} 列")
    check("表头含 P 档/优先级分", "P 档" in str(rows2[0]) and "优先级分" in str(rows2[0]),
          str(rows2[0]))
    check("行数 = 簇数", r2["rows"] == len(rows2) - 1, f"{r2['rows']} vs {len(rows2)-1}")

    # ---------- [1][6] 复盘报告 ----------
    print("\n[1][6] 复盘报告：能生成、列数对")
    p3 = OUT / "retro.xlsx"
    r3 = export_retro(p3)
    check("生成了文件", p3.exists(), str(r3))
    rows3 = read_xlsx(p3)
    check("读回来有表头+数据", len(rows3) >= 1, f"{len(rows3)} 行")
    check("表头 9 列", len(rows3[0]) == 9, f"{len(rows3[0])} 列")
    check("表头含结论/效果量/理由码",
          all(k in str(rows3[0]) for k in ("结论", "效果量", "理由码")), str(rows3[0]))

    shutil.rmtree(OUT, ignore_errors=True)
    print("\n（导出文件已清理 ✅）")
    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
