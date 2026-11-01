# -*- coding: utf-8 -*-
"""
退款率数据检查（probe_refund_corr.py）—— 阶段 3.4 的**第一步**
============================================================
路线图 §8.4 的原话：

    "**本阶段第一步是"先看数据"，不是写规则**"
    "mock 的退款金额是独立随机数（refund = gmv * uniform(0.01,0.03)）
     → **和差评无关** ✅
     所以 G1（原本 POSITIVE）很可能被"退款率不降"卡成 INCONCLUSIVE
     先跑一个检查脚本：按组统计 基期/干预后 的退款率，看有没有跟着差评一起动
     两种结果两种做法：
       ① 有相关性 → 直接实现 8.2 的规则 ✅
       ② 没相关性 → 要么给 mock 加相关性（数据改动单 ⚠️），
                    要么 refund_rate 只作"展示"、不参与判定（并在文档写明理由）
     **不要先写规则再看数据** —— 那是"让数据符合结论" ✅"

跑法：python -m scripts.probe_refund_corr
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import case, func, select          # noqa: E402

from hub.persistence.db import get_session         # noqa: E402
from hub.persistence.models import ActionOutcome   # noqa: E402
from hub.persistence.models import Record as RecordRow  # noqa: E402
from hub.persistence.models import SalesDaily      # noqa: E402

BAD = 2


def _review_rate(spus: list[str], lo: date, hi: date) -> tuple[int, int]:
    """(评价数, 差评数)，口径和 retro.metrics.per_spu_counts 一致 ✅"""
    if not spus:
        return 0, 0
    from datetime import datetime, timezone
    lo_dt = datetime.combine(lo, datetime.min.time(), tzinfo=timezone.utc)
    hi_dt = datetime.combine(hi, datetime.min.time(), tzinfo=timezone.utc)
    is_bad = case((RecordRow.rating <= BAD, 1), else_=0)
    with get_session() as s:
        n, bad = s.execute(
            select(func.count(), func.sum(is_bad))
            .where(RecordRow.platform_spu_id.in_(spus),
                   RecordRow.dqe_decision == "keep",
                   RecordRow.review_time >= lo_dt,
                   RecordRow.review_time < hi_dt)
        ).one()
    return int(n or 0), int(bad or 0)


def _sales_rates(spus: list[str], lo: date, hi: date) -> dict:
    """(GMV, 件数, 退款件数, 访客, 转化率) 的窗口汇总。

    ⚠️ `sales_daily` **没有 `buyers` 列**（2026-10-31 核对 ✅）
       → 支付买家数 = conversion × uv（表里两者都有 ✅）
    """
    if not spus:
        return {}
    with get_session() as s:
        row = s.execute(
            select(func.sum(SalesDaily.gmv), func.sum(SalesDaily.units),
                   func.sum(SalesDaily.refund_units), func.sum(SalesDaily.uv))
            .where(SalesDaily.canonical_spu_id.in_(spus),
                   SalesDaily.stat_date >= lo, SalesDaily.stat_date < hi)
        ).one()
    gmv, units, refund, uv = (float(row[0] or 0), int(row[1] or 0),
                              int(row[2] or 0), int(row[3] or 0))
    return {"gmv": gmv, "units": units, "refund_units": refund, "uv": uv,
            "refund_rate": round(refund / units, 4) if units else None}


def main() -> int:
    print("=" * 76)
    print("  退款率 vs 差评率：数据里有没有相关性？（阶段 3.4 第一步）")
    print("=" * 76)

    with get_session() as s:
        outs = s.scalars(select(ActionOutcome)).all()
        groups = []
        for o in outs:
            treat = o.treat_canonical_spu_id
            ctrl = list(o.control_spu_ids or [])
            if not treat:
                continue
            groups.append({
                "proposal": str(o.proposal_id)[:8],
                "status": o.status, "effect": o.effect,
                "baseline": (o.baseline_start, o.baseline_end),
                "observe_end": o.observe_end,
                "treat": treat, "ctrl": ctrl,
            })

    if not groups:
        print("  [!] 没有 action_outcome 记录 —— 先跑 hub retro / 全链")
        return 1

    print(f"\n  共 {len(groups)} 条 observation\n")
    print(f"  {'组':<10}{'差评率 基期→干预后':<26}{'退款率 基期→干预后':<26}{'结论'}")
    print("-" * 76)

    rows = []
    for g in groups:
        bs, be = g["baseline"]
        oe = g["observe_end"]
        if not bs or not be or not oe:
            continue
        # 干预后窗口：从 baseline_end 到 observe_end ✅
        spus = [g["treat"]] if g["treat"] else []
        n0, bad0 = _review_rate(spus, bs, be)
        n1, bad1 = _review_rate(spus, be, oe)
        s0 = _sales_rates(spus, bs, be)
        s1 = _sales_rates(spus, be, oe)
        neg0 = round(bad0 / n0, 4) if n0 else None
        neg1 = round(bad1 / n1, 4) if n1 else None
        ref0, ref1 = s0.get("refund_rate"), s1.get("refund_rate")
        rows.append({"g": g, "neg0": neg0, "neg1": neg1, "ref0": ref0, "ref1": ref1,
                     "n0": n0, "n1": n1, "status": g["status"], "effect": g["effect"]})
        def fmt(a, b):
            if a is None or b is None:
                return "  数据不足"
            arrow = "↓" if b < a else ("↑" if b > a else "=")
            return f"{a:.4f} → {b:.4f} {arrow}"
        print(f"  {g['proposal']:<10}{fmt(neg0, neg1):<26}{fmt(ref0, ref1):<26}{g['effect'] or g['status']}")

    # ---------------- 相关性检查（核心） ----------------
    print("\n" + "=" * 76)
    print("  相关性检查：差评率变化 vs 退款率变化")
    print("=" * 76)
    pairs = [(r["neg1"] - r["neg0"], r["ref1"] - r["ref0"])
             for r in rows
             if None not in (r["neg0"], r["neg1"], r["ref0"], r["ref1"])]
    if len(pairs) < 3:
        print(f"  ⚠️ 可用样本只有 {len(pairs)} 个（<3）→ **判断不了** ✅")
        print("     这一阶段不能「先写规则」，只能把 refund_rate 当展示项 ✅")
        return 2
    same_dir = sum(1 for dn, dr in pairs if (dn > 0) == (dr > 0))
    print(f"  可用样本：{len(pairs)} 个")
    print(f"  **同向变化**（差评↑退款↑ 或 差评↓退款↓）：{same_dir}/{len(pairs)}")
    for i, (dn, dr) in enumerate(pairs, 1):
        print(f"    #{i}  Δ差评={dn:+.4f}　Δ退款={dr:+.4f}　{'同向' if (dn>0)==(dr>0) else '反向'}")
    print()
    if same_dir >= len(pairs) * 0.75:
        print("  → **有相关性**（≥75% 同向）→ 按 §8.2 实现规则 ✅")
    elif same_dir <= len(pairs) * 0.5:
        print("  → **没有相关性**（≤50% 同向）→ 按 §8.4 的第 ② 条：")
        print("     refund_rate **只作展示，不参与判定**，并在文档写明理由 ✅")
    else:
        print("  → **弱相关**（50~75%）→ 需要更多样本；建议先只作展示 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
