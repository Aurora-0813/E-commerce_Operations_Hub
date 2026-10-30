# -*- coding: utf-8 -*-
"""
看复盘结果（show_outcomes.py）
==============================
把 `action_outcome` 里的复盘结论按提案汇总打印。

为什么要单独一个脚本：
    用 `python -c "..."` 写这种多行带引号的代码，在 PowerShell 里会被
    `\"` 转义搞坏（实测踩过：SyntaxError: unterminated string literal）。
    凡是"超过一行的临时查询"，都该落成文件——省得下次再踩。

运行（在 backend/ 目录下）：
    python scripts/show_outcomes.py
"""
import sys
from collections import defaultdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                                  # noqa: E402

from hub.persistence.db import get_session                      # noqa: E402
from hub.persistence.models import ActionOutcome, ActionProposal  # noqa: E402

_MARK = {"POSITIVE": "✅", "NEGATIVE": "❌", "INCONCLUSIVE": "⚪"}


def main() -> None:
    with get_session() as s:
        outcomes = list(s.scalars(select(ActionOutcome)).all())
        props = {p.id: p for p in s.scalars(select(ActionProposal)).all()}

    by_proposal: dict = defaultdict(list)
    for o in outcomes:
        by_proposal[o.proposal_id].append(o)

    done = {pid: rows for pid, rows in by_proposal.items()
            if any(r.status == "DONE" for r in rows)}
    waiting = {pid: rows for pid, rows in by_proposal.items() if pid not in done}

    print(f"已复盘 {len(done)} 个动作｜等观察窗口 {len(waiting)} 个动作\n")
    for pid, rows in done.items():
        r = rows[0]
        e = r.effect or {}
        p = props.get(pid)
        tool = getattr(p, "tool_name", "?")
        mark = _MARK.get(r.conclusion, "?")
        print(f"  {mark} {r.conclusion:<13} {tool:<20} "
              f"｜{len(rows)} 个商品｜{r.reason_code}")
        if e.get("did_pp") is not None:
            print(f"       DID {e['did_pp']:+.2f}pp"
                  f"　[{e['ci_low']:+.2f}, {e['ci_high']:+.2f}]"
                  f"　n={e.get('n_reviews', 0)} 条评价")
        print(f"       {e.get('detail', '')}")

    if waiting:
        print(f"\n还在等观察窗口（{len(waiting)} 个）：")
        for pid, rows in list(waiting.items())[:8]:
            p = props.get(pid)
            print(f"  · {getattr(p, 'tool_name', '?'):<20} "
                  f"{len(rows)} 个商品｜观察到 {rows[0].observe_end}")


if __name__ == "__main__":
    main()
