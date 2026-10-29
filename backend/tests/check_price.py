# -*- coding: utf-8 -*-
"""
查看价格背离检测的结果（anomaly 表，type='price'）。

运行（在 backend/ 目录下，先跑 hub price）：
    python tests/check_price.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                  # noqa: E402

from hub.persistence.db import get_session     # noqa: E402
from hub.persistence.models import Anomaly     # noqa: E402


def main() -> None:
    with get_session() as s:
        rows = s.scalars(
            select(Anomaly)
            .where(Anomaly.type == "price")
            .order_by(Anomaly.detected_at.desc())
        ).all()

    if not rows:
        print("anomaly 表里没有价格异常（先跑 python -m hub.cli price）")
        return

    latest = rows[0].run_id
    current = [a for a in rows if a.run_id == latest]

    print(f"最近一次检测：{len(current)} 个价格背离"
          f"（表里累计 {len(rows)} 行）\n")

    for a in current:
        m = a.metric or {}
        ev = a.evidence or {}
        prices = ev.get("prices", {})
        rates = ev.get("review_rates", {})
        print(f"【{ev.get('title', '?')}】{a.canonical_spu_id}")
        print(f"  类型   {m.get('kind')}")
        print(f"  价格   淘宝 {prices.get('taobao', '?')} / 京东 {prices.get('jd', '?')}"
              f"（贵 {m.get('disparity_pct')}%）")
        print(f"  差评率 淘宝 {rates.get('taobao', 0):.1%} / 京东 {rates.get('jd', 0):.1%}")
        print(f"  dev    {m.get('dev')}｜分位 {m.get('sev')}｜score {m.get('score')}")
        print(f"  贵的一侧 {m.get('expensive_platform')}")
        print()

    print("=== 汇总 ===")
    kinds = {}
    for a in current:
        k = (a.metric or {}).get("kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    for k, n in kinds.items():
        print(f"  {k}: {n}")

    ok = 3 <= len(current) <= 5
    print("\n" + (f"✅ 检出 {len(current)} 个，符合预期（3~5 个）"
                  if ok else f"⚠️ 检出 {len(current)} 个，预期 3~5 个"))


if __name__ == "__main__":
    main()
