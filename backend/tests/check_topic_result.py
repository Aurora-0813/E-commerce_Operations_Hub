# -*- coding: utf-8 -*-
"""
查看 M3 的最新一次运行结果（analysis_run.stats）。

运行（在 backend/ 目录下）：
    python tests/check_topic_result.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                  # noqa: E402

from hub.persistence.db import get_session     # noqa: E402
from hub.persistence.models import AnalysisRun  # noqa: E402


def main() -> None:
    with get_session() as s:
        run = s.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.trace_id.isnot(None))
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )

    if run is None:
        print("没有 analysis_run 记录")
        return

    stats: dict = run.stats or {}
    print(f"trace_id : {run.trace_id}")
    print(f"status   : {run.status}")
    print(f"created  : {run.created_at}")

    summary = stats.get("summary") or {}
    print(f"\n=== 运行统计 ===")
    for k, v in summary.items():
        print(f"  {k:<18} {v}")

    print(f"\n=== 主题分布 ===")
    for k, v in (stats.get("by_topic") or {}).items():
        print(f"  {k:<16} {v}")
    print(f"\n=== 平台分布 ===")
    for k, v in (stats.get("by_platform") or {}).items():
        print(f"  {k:<16} {v}")

    print(f"\n=== 归因结果（{len(stats.get('attributions') or [])} 条）===")
    for a in stats.get("attributions") or []:
        print(f"\n【{a['root_cause']} / {a['platform']}】"
              f"置信 {a['confidence']}｜证据 {len(a['evidence'])} 条")
        print(f"  解释：{a['explanation']}")
        st = a.get("stats") or {}
        if st.get("by_category"):
            cats = "、".join(f"{k} {v}" for k, v in st["by_category"].items())
            print(f"  品类：{cats}（占总量 {st.get('share', 0):.1%}）")
        for ev in a["evidence"]:
            print(f"    · {ev['quote'][:46]}")

    print(f"\n=== 跳过的格子 ===")
    for c in stats.get("cells") or []:
        if c.get("skipped"):
            print(f"  {c['topic']}/{c['platform']}（{c['count']} 条）：{c.get('reason')}")


if __name__ == "__main__":
    main()
