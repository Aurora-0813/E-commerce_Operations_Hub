# -*- coding: utf-8 -*-
"""
DID 诊断（diagnose_did.py）
===========================
**为什么 G1 明明降了 45%，却被判"不显著"？**

`hub retro` 只打印四个比率，看不出"这个比率有多可信"。
这个脚本把每个商品的原始计数打出来，让人能判断：

    · 每个窗口到底有多少条评价（21 条？还是 3 条？）
    · 对照组那 8.6 个百分点的下降，是**整体系统性**的，还是**少数商品**造成的

两种结论对应两种完全不同的修法：
    整体系统性下降 → 数据里真的有趋势（生成器有 bug，或窗口里混了别的事件）
    少数商品造成   → 就是抽样噪声，应该加长窗口 / 加多商品，而不是改公式

运行（在 backend/ 目录下）：
    python scripts/diagnose_did.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                              # noqa: E402

from hub.persistence.db import get_session                  # noqa: E402
from hub.persistence.models import ActionOutcome            # noqa: E402
from hub.retro.metrics import build_input, per_spu_counts   # noqa: E402


def _row(tag: str, c: dict) -> str:
    nb, na = c["n_before"], c["n_after"]
    rb = c["bad_before"] / nb if nb else 0
    ra = c["bad_after"] / na if na else 0
    return (f"  {tag:<12} 前 {c['bad_before']:>3}/{nb:<3} = {rb:>6.1%}"
            f"   后 {c['bad_after']:>3}/{na:<3} = {ra:>6.1%}"
            f"   差 {(ra - rb) * 100:>+6.1f}pp")


def main() -> None:
    with get_session() as s:
        outcomes = list(s.scalars(select(ActionOutcome)).all())

    # 只看历史那 4 组（窗口已过期）
    hist = [o for o in outcomes if o.observe_end < date.today()]
    if not hist:
        print("没有到期的历史干预")
        return

    base_start = min(o.baseline_start for o in hist)
    base_end = max(o.baseline_end for o in hist)
    obs_end = max(o.observe_end for o in hist)
    obs_start = base_end + timedelta(days=1)

    print(f"窗口：前 {base_start}~{base_end}　后 {obs_start}~{obs_end}\n")

    treated_all = sorted({o.treat_canonical_spu_id for o in hist})
    control = sorted({o.treat_canonical_spu_id for o in outcomes}
                     - set(treated_all))
    data = build_input(treated_all, control, base_start, base_end, obs_start, obs_end)

    print("=== 处理组（10 个商品，逐商品看）===")
    for spu, c in sorted(data.treated.items()):
        print(_row(spu, c))

    print("\n=== 对照组（逐商品看）===")
    for spu, c in sorted(data.control.items()):
        print(_row(spu, c))

    # 关键判断：对照组的下降是"整体"还是"少数商品"
    deltas = []
    for spu, c in data.control.items():
        if c["n_before"] and c["n_after"]:
            deltas.append(c["bad_after"] / c["n_after"] - c["bad_before"] / c["n_before"])
    if deltas:
        neg = sum(1 for d in deltas if d < 0)
        print(f"\n对照组：{len(deltas)} 个商品里 {neg} 个下降")
        print(f"  平均差 {sum(deltas) / len(deltas) * 100:+.1f}pp"
              f"｜最大降 {min(deltas) * 100:+.1f}pp｜最大升 {max(deltas) * 100:+.1f}pp")
        print("\n判读：")
        print("  · 如果只有 1~2 个商品大幅下降 → 抽样噪声，加长窗口即可")
        print("  · 如果 8~9 个商品都在降 → 数据里有系统性趋势，去查生成器")


if __name__ == "__main__":
    main()
