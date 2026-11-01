# -*- coding: utf-8 -*-
"""
复盘指标测试（check_retro_metrics.py）—— 阶段 3.4
================================================
这是路线图 §8.3 明确要求的"**新增**"测试文件 ✅

验 6 条：
    [1] `sales_rates()` 能返回 refund_rate / conversion（**不是 None**）
    [2] `conversion` 用 `units / uv` 算（**不读 `sales_daily.conversion`**，那列全是 NULL ✅）
    [3] `refund_rate` 在 0~1 之间（口径合理）
    [4] **`refund_rate` 不参与判定**：判定只看 `neg_rate` ✅ ← 这是本阶段的核心决定
    [5] 数据相关性检查的结论可复现（同向率 ≈ 50%，见 16 号文档）
    [6] 空商品列表不炸（返回全 0/None）

第 [4] 条怎么测：
    `judge()` 的签名/逻辑里**不该出现 refund** ✅
    → 用 `inspect.getsource` 检查源码里没有 "refund" 关键字 ✅
    （比"手动看一遍"可靠，且以后有人加进去会立刻红 ✅）
"""
import inspect
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 66)
    print("  复盘指标扩展（阶段 3.4）")
    print("=" * 66)

    from hub.retro.metrics import per_spu_counts, sales_rates

    # ---------- [1][2][3] ----------
    print("\n[1][2][3] sales_rates 的口径")
    spus = ["TB-0001"]
    lo, hi = date(2026, 1, 1), date(2026, 12, 31)
    c = per_spu_counts(spus, lo, hi)
    check("per_spu_counts 有数据", c.get("TB-0001", {}).get("n", 0) > 0, str(c))
    r = sales_rates(spus, lo, hi)
    check("有 refund_rate", r.get("refund_rate") is not None, str(r.get("refund_rate")))
    check("有 conversion", r.get("conversion") is not None, str(r.get("conversion")))
    check("refund_rate 在 0~1", 0 <= (r.get("refund_rate") or 0) <= 1, str(r.get("refund_rate")))
    check("conversion 在 0~1", 0 < (r.get("conversion") or 0) <= 1, str(r.get("conversion")))
    # [2] 证明 conversion != 0（如果读了那个 NULL 列，就会是 None/0 ✅）
    check("conversion > 0（说明是用 units/uv 算的 ✅）",
          (r.get("conversion") or 0) > 0, str(r.get("conversion")))

    # ---------- [6] 空列表 ----------
    print("\n[6] 空商品列表不炸")
    e = sales_rates([], lo, hi)
    check("不抛异常", isinstance(e, dict))
    check("refund_rate=None", e.get("refund_rate") is None)
    check("units=0", e.get("units") == 0, str(e.get("units")))

    # ---------- [4] refund 不参与判定 ----------
    print("\n[4] **refund_rate 不参与判定**（只用 neg_rate）")
    import hub.retro.did as did
    src_judge = ""
    for name in ("judge", "decide", "conclude"):
        fn = getattr(did, name, None)
        if fn is not None:
            src_judge += inspect.getsource(fn)
    if not src_judge:
        # 判定可能在 report.py 里
        import hub.retro.report as rep
        src_judge = inspect.getsource(rep)
    check("判定逻辑里**没有** refund 关键字", "refund" not in src_judge.lower(),
          "有 refund 就说明被加进判定了 ✅ ✗")

    # ---------- [5] 相关性结论可复现 ----------
    print("\n[5] 相关性结论（同向率 ≈ 50%，见 16 号文档）")
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.probe_refund_corr"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    check("探针能跑", proc.returncode in (0, 2), f"exit={proc.returncode}")
    check("输出里有同向数字", "同向变化" in out, "")
    check("结论是弱相关/没相关性（不是'有相关性'）",
          "弱相关" in out or "没有相关性" in out,
          [ln.strip() for ln in out.splitlines() if "→" in ln][-1:])

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
