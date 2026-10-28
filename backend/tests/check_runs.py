# -*- coding: utf-8 -*-
"""
列出最近的 analysis_run（M2 的销毁证据 + M3 的归因统计）。

为什么需要：
    "我们宣称阅后即焚" → 要能拿出证据。
    M2 现在每次运行都写一条 analysis_run，其中 residual=0 就是销毁成功的证明。

运行（在 backend/ 目录下）：
    python tests/check_runs.py
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
        runs = s.scalars(
            select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(10)
        ).all()

    if not runs:
        print("没有 analysis_run 记录")
        return

    print(f"最近 {len(runs)} 次运行：\n")
    for r in runs:
        st: dict = r.stats or {}
        module = st.get("module", "?")

        print(f"{r.created_at:%Y-%m-%d %H:%M:%S}  [{module}] {r.status:<9} "
              f"trace={r.trace_id[:12]}…")

        if module == "M2":
            print(f"    图片 {st.get('images')} 张｜剥离 {st.get('stripped')} 张｜"
                  f"销毁 {st.get('destroyed')} 个｜"
                  f"残留 {st.get('residual')} 个 ← 0 才算销毁成功")
            print(f"    Vision {st.get('vision_model')}｜调用 {st.get('vision_calls')} 次｜"
                  f"失败 {st.get('failed')}")
        elif module == "M3":
            sm = st.get("summary") or {}
            print(f"    分类 {sm.get('classified')} 条｜归因 {sm.get('attributed')} 格｜"
                  f"证据 {sm.get('evidence_total')} 条｜丢弃 {sm.get('evidence_dropped')}")
            print(f"    兜底 {sm.get('fallback')}｜批次数 {sm.get('batches')}")
        else:
            print(f"    stats 键：{sorted(st.keys())}")
        print()


if __name__ == "__main__":
    main()
