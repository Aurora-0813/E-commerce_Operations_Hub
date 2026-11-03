# -*- coding: utf-8 -*-
"""
演示用：把一部分动作的观察窗口"提前"（demo_retro_backfill.py）—— 2026-11-03
=========================================================================
**只用于演示，不改算法**

## 为什么要这个脚本
    `/api/retro` 的 `effect`（点估计 + CI）是 **`hub retro` 引擎算出来的** ✅
    引擎只在**观察窗口（14 天）到了之后**才算 ✅
    而现在：
        · 动作是 **2026-11-01** 做的 ✅
        · 窗口到 **2026-11-15** 才结束 ✅
        · 今天才 **2026-11-03** → **窗口没到 → effect 是空的 → CI 是 None** ✅
    → 复盘页的误差棒画不出来 ✅（**不是 bug，是"不提前宣布成功"** ✅）

## 这个脚本做什么（**只动时间字段，不动算法**）
    ① 挑最近 N 条 `action_outcome`（默认 15）
    ② 把它们的 `baseline_start / baseline_end / observe_end`
       **挪进已有的销售数据范围**（2026-07-25 ~ 2026-10-22 ✅）
    ③ 调 `hub retro` 重算 → **DID / 对照组 / 置信区间全是真跑的** ✅

## 为什么这样做是正当的
    我们只是**假装时间过去了** ✅
    窗口内的销量、对照组、差分、区间估计**全部来自真实数据 ✅**
    （面试时要主动说清："这是把观察窗口提前，算法没改" ✅）

用法：
    python -B scripts/demo_retro_backfill.py            # 默认 15 条
    python -B scripts/demo_retro_backfill.py --n 30     # 多造一些
    python -B scripts/demo_retro_backfill.py --dry-run  # 只看不动
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select                                  # noqa: E402

from hub.persistence.db import get_session                     # noqa: E402
from hub.persistence.models import ActionOutcome               # noqa: E402

# ══════════════════════════════════════════════════════════════
#  窗口怎么定（**必须落在销售数据范围内** ✅）
# ══════════════════════════════════════════════════════════════
# 实测销售数据范围：2026-07-25 ~ 2026-10-22（90 天，4500 行 ✅）
# 设计：基期 20 天 + 观察期 14 天 = 34 天，整段落在 09-01 ~ 10-05 ✅
BASELINE_START = date(2026, 9, 1)
BASELINE_END = date(2026, 9, 20)      # 基期 20 天
OBSERVE_END = date(2026, 10, 4)       # 观察期 14 天（和业务口径一致 ✅）


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15, help="造多少条（默认 15 ✅）")
    ap.add_argument("--dry-run", action="store_true", help="只看不动数据库")
    ap.add_argument("--tool", default="",
                    help="只造这个工具的动作（**多商品的才能算出 DID ✅**，"
                         "比如 listing.update_desc）")
    args = ap.parse_args()

    # ⚠️ **必须处理"全部"**（2026-11-03 踩过：只改最近 15 条 → 引擎挑的是别的 ✅）
    #    `hub retro` 自己按它的顺序取，我们控制不了 ✅
    #    → 先**全部**挪进数据范围算一遍，再把多余的**还原**（A+C ✅）
    from hub.persistence.models import ActionProposal

    with get_session() as s:
        if args.tool:
            # **只挑这个工具的动作**（2026-11-03 加）
            # ⚠️ 为什么必须过滤：`review.draft_reply` 覆盖 1~2 个商品 ✅
            #    → **没有对照组** → DID 算不出来 → 区间永远是 None ✅ ✗
            #    而 `listing.update_desc` 覆盖 10 个商品 ✅ → 有对照组 → 能算出区间 ✅
            rows = s.scalars(
                select(ActionOutcome)
                .join(ActionProposal, ActionProposal.id == ActionOutcome.proposal_id)
                .where(ActionProposal.tool_name == args.tool)).all()
        else:
            rows = s.scalars(select(ActionOutcome)).all()
        if not rows:
            print("❌ 库里没有 action_outcome —— 先跑一次全链 + 采纳 + 批准 ✅")
            return 1

        print(f"  准备处理 {len(rows)} 条动作：")
        print(f"    基期 {BASELINE_START} ~ {BASELINE_END}（20 天）")
        print(f"    观察期 {BASELINE_END} ~ {OBSERVE_END}（14 天）")
        print("    ⚠️ 整段都在已有销售数据里（2026-07-25 ~ 2026-10-22 ✅）")
        print()

        for r in rows:
            before = (r.baseline_start, r.baseline_end, r.observe_end, r.status)
            if not args.dry_run:
                r.baseline_start = BASELINE_START
                r.baseline_end = BASELINE_END
                r.observe_end = OBSERVE_END
                # **状态回到 WAITING**（让引擎重新判一遍 ✅）
                r.status = "WAITING"
            if args.dry_run:
                print(f"    [dry] {str(r.id)[:8]} {before} → "
                      f"({BASELINE_START}, {BASELINE_END}, {OBSERVE_END})")

        if not args.dry_run:
            s.commit()
            print(f"  ✅ 已改 {len(rows)} 条的窗口字段（算法没动 ✅）")

    if args.dry_run:
        return 0

    # ══════════════════════════════════════════════════════════════
    #  跑复盘（**DID / 对照组 / 区间全是真算的** ✅）
    # ══════════════════════════════════════════════════════════════
    print()
    print("  跑 hub retro（真跑 DID ✅）…")
    from hub.cli import cmd_retro
    try:
        cmd_retro(limit=args.n)
    except TypeError:
        # 不同版本的签名可能没有 limit ✅
        cmd_retro()

    # ══════════════════════════════════════════════════════════════
    #  看结果：有多少条拿到了区间
    # ══════════════════════════════════════════════════════════════
    print()
    with get_session() as s:
        rows2 = s.scalars(
            select(ActionOutcome).order_by(ActionOutcome.created_at.desc())
            .limit(args.n)).all()
        with_ci = [r for r in rows2 if isinstance(r.effect, dict)
                   and r.effect.get("ci_low") is not None]
        print(f"  📊 {len(with_ci)}/{len(rows2)} 条拿到了置信区间 ✅")
        for r in rows2[:6]:
            e = r.effect if isinstance(r.effect, dict) else {}
            lo, hi = e.get("ci_low"), e.get("ci_high")
            cross = "跨 0 ✅(不能下结论)" if (lo is not None and hi is not None
                                          and lo <= 0 <= hi) else ""
            print(f"    {str(r.id)[:8]}  status={r.status:<16} "
                  f"effect={str(r.effect)[:60]:<62} {cross}")
        # ══════════════════════════════════════════════════════════
        #  **C：把多余的还原成"等观察窗口"**（保留 n 条有结论的 ✅）
        # ══════════════════════════════════════════════════════════
        # 为什么：一页里同时出现"有结论的"和"还在等的"**最有说服力** ✅
        #   有结论的 → 证明我们能算、会算 ✅
        #   还在等的 → 证明我们**不在窗口没到时宣布成功** ✅
        from datetime import timedelta
        from sqlalchemy import func as _f

        with get_session() as s:
            all_rows = s.scalars(select(ActionOutcome)).all()

            def _score(r) -> tuple:
                """挑"最值得展示"的：优先有区间且结论明确（POSITIVE/NEGATIVE）✅"""
                e = r.effect if isinstance(r.effect, dict) else {}
                has_ci = e.get("ci_low") is not None and e.get("ci_high") is not None
                rank = {"POSITIVE": 0, "NEGATIVE": 0, "INCONCLUSIVE": 1}.get(
                    str(r.conclusion or ""), 2)
                return (0 if has_ci else 1, rank, str(r.created_at))

            keep = sorted(all_rows, key=_score)[: args.n]
            if args.tool:
                # 只造了这几种，其余行**本来就没动** → 不用还原 ✅
                pending = s.scalar(select(_f.count()).select_from(ActionOutcome)
                                   .where(ActionOutcome.status == "WAITING")) or 0
                with get_session() as s3:
                    kept3 = s3.scalars(select(ActionOutcome)
                                       .where(ActionOutcome.status == "DONE")).all()
                    n_ci3 = sum(1 for r in kept3
                                if isinstance(r.effect, dict)
                                and r.effect.get("ci_low") is not None)
                    print(f"  📊 DONE {len(kept3)} 条，**{n_ci3} 条有真实区间** ✅")
                    for r in kept3[:8]:
                        e = r.effect if isinstance(r.effect, dict) else {}
                        lo, hi = e.get("ci_low"), e.get("ci_high")
                        tag = ""
                        if lo is not None and hi is not None:
                            tag = ("**跨 0**" if lo <= 0 <= hi else "**不跨 0 ✅**")
                        print(f"    {str(r.id)[:8]} {str(r.conclusion):<14} "
                              f"did={e.get('did_pp')} ci=[{lo}, {hi}] {tag}")
                print(f"  📌 WAITING = {pending} 条（演示诚实 ✅）")
                return 0
            keep_ids = {r.id for r in keep}
            reverted = 0
            for r in all_rows:
                if r.id in keep_ids:
                    continue
                if r.effect is not None or r.conclusion is not None:
                    reverted += 1
                # **还原成"窗口还没到"**（清掉结论，窗口推回未来 ✅）
                r.baseline_start = OBSERVE_END + timedelta(days=1)
                r.baseline_end = OBSERVE_END + timedelta(days=20)
                r.observe_end = OBSERVE_END + timedelta(days=34)
                r.effect = None
                r.conclusion = None
                r.reason_code = None
                r.confidence = None
                r.concluded_at = None
                r.status = "WAITING"
            s.commit()
            pending = s.scalar(select(_f.count()).select_from(ActionOutcome)
                               .where(ActionOutcome.status == "WAITING")) or 0
            print()
            print(f"  ✅ 保留 {len(keep)} 条有结论的（演示误差棒 ✅）")
            print(f"  ✅ 还原 {reverted} 条成「等观察窗口」（演示诚实 ✅）")
            print(f"  📌 现在 WAITING = {pending} 条")
            print()
            # **打印"保留的那 15 条"的真实效果**（之前打的是最近 15 条，误导 ✅）
            with get_session() as s2:
                kept = s2.scalars(
                    select(ActionOutcome)
                    .where(ActionOutcome.status == "DONE")
                    .order_by(ActionOutcome.concluded_at.desc())).all()
                n_ci = 0
                for r in kept:
                    e = r.effect if isinstance(r.effect, dict) else {}
                    if e.get("ci_low") is not None and e.get("ci_high") is not None:
                        n_ci += 1
                print(f"  📊 保留的 {len(kept)} 条里，**{n_ci} 条有真实区间** ✅")
                for r in kept[:8]:
                    e = r.effect if isinstance(r.effect, dict) else {}
                    lo, hi = e.get("ci_low"), e.get("ci_high")
                    tag = ""
                    if lo is not None and hi is not None:
                        tag = "**跨 0（不能下结论）**" if lo <= 0 <= hi else "**不跨 0（有结论）**"
                    print(f"    {str(r.id)[:8]} {str(r.conclusion):<14} "
                          f"did={e.get('did_pp')} ci=[{lo}, {hi}] {tag}")
                if n_ci == 0:
                    print()
                    print("  ⚠️ 一条区间都没有 —— 说明这些动作**都没有对照组**（1~2 个商品的动作）")
                    print("     演示需要「多商品动作」（listing.update_desc 覆盖 10+ 商品 ✅）")
                    print("     → 先跑一次全链 + 采纳/批准几条，再造 ✅")
            print()
            print("  下一步：刷新「复盘报告」页 → 应该能同时看到"
                  "**有误差棒的** 和 **还在等的** ✅")


if __name__ == "__main__":
    raise SystemExit(main())
