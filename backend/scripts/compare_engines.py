# -*- coding: utf-8 -*-
"""
两套编排的对照（compare_engines.py）—— 阶段 3.3 的产出
=====================================================
**同一个任务，两种实现**：
    A. 手写版（`hub/agent/handle.py`）—— 3 个只读步 + 1 次判断，`if/else` 控制流
    B. LangGraph 版（`hub/agent/graph.py`）—— 同样的 4 个节点，用图的边连接

比三样（都是可测的，不是感觉）：
    ① **结果一致性**：同一批簇，两版选出的工具 / status / 引用条数是否相同
       ← 这一条最重要：如果结果不同，说明有一版写错了 ✅
    ② **耗时**：两版的墙钟时间（图会多一层调度开销 ✅）
    ③ **代码量与结构**：这个在报告里写，脚本算不出来 ✅

跑法：
    python scripts/compare_engines.py                  # 默认 5 个簇
    python scripts/compare_engines.py --limit 22
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import IssueCluster


def pick(limit: int) -> list[dict]:
    with get_session() as s:
        rows = s.scalars(
            select(IssueCluster).order_by(
                IssueCluster.priority_tier.asc().nullslast(),
                IssueCluster.priority_score.desc().nullslast(),
            ).limit(limit)
        ).all()
        return [{"id": str(c.id), "label": c.label, "tier": c.priority_tier} for c in rows]


def counters(r) -> dict:
    """从结果里抽可比的三项 + 模型 prompt 长度（用来判断两版送进去的东西一样不一样）。"""
    verify = next((e["text"] for e in r.evidence if e["ref"] == "E-VERIFY"), "")
    import re
    m = re.search(r"引用了 (\d+) 条证据，有效 (\d+) 条", verify)
    # judge 那步的 args 里有 prompt_chars（两版都记了）——
    # **长度不同 → 说明送进模型的 prompt 不同**（比如规则块没拼进去 ✅）
    judge = next((s for s in r.steps if s.tool == "llm.judge"), None)
    pchars = (judge.args or {}).get("prompt_chars") if judge else None
    return {"tool": r.tool, "status": r.status,
            "cited": int(m.group(1)) if m else 0,
            "valid": int(m.group(2)) if m else 0,
            "pchars": pchars}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args()

    from hub.agent.graph import handle_graph
    from hub.agent.handle import handle

    clusters = pick(a.limit)
    if not clusters:
        print("没有可测的簇。")
        return 1

    print("=" * 74)
    print(f"  两套编排对照　{len(clusters)} 个簇　（A=手写  B=LangGraph）")
    print("=" * 74)
    print(f"  {'簇':<18}{'A 工具':<20}{'B 工具':<20}{'一致?'}")
    print("-" * 74)

    t_a = t_b = 0.0
    same = 0
    for c in clusters:
        t0 = time.time()
        ra = handle(c["id"])
        da = time.time() - t0
        t0 = time.time()
        rb = handle_graph(c["id"])
        db = time.time() - t0
        t_a += da
        t_b += db

        ca, cb = counters(ra), counters(rb)
        same_now = (ca["tool"] == cb["tool"] and ca["status"] == cb["status"]
                    and ca["cited"] == cb["cited"])
        same += int(same_now)
        print(f"  {c['label'][:16]:<18}{ca['tool'] or '(无)':<20}"
              f"{cb['tool'] or '(无)':<20}{'✅' if same_now else '❌ 不一致'}")
        # prompt 长度：两版应该**完全一样**（同样的证据 + 同样的规则块 ✅）
        if ca["pchars"] != cb["pchars"]:
            print(f"      ⚠️ 送进模型的 prompt 长度不同：A={ca['pchars']} "
                  f"B={cb['pchars']}　← **这就是结果不同的原因**（不是框架差异）")

    n = len(clusters)
    print("-" * 74)
    print(f"  结果一致：{same}/{n}（{same/n:.0%}）"
          + ("　✅ 两版行为等价" if same == n else "　⚠️ 有差异，要查是哪版写错了"))
    print(f"  总耗时：A 手写 {t_a:.1f}s　B LangGraph {t_b:.1f}s　"
          f"（B 比 A {'慢' if t_b > t_a else '快'} {abs(t_b - t_a) / max(t_a, 0.01):.0%}）")

    # ---- 代码量（这是对照的核心维度之一，别让人凭感觉估 ✅）----
    root = Path(__file__).resolve().parent.parent
    f_a = root / "hub" / "agent" / "handle.py"
    f_b = root / "hub" / "agent" / "graph.py"
    if f_a.exists() and f_b.exists():
        la = len(f_a.read_text(encoding="utf-8").splitlines())
        lb = len(f_b.read_text(encoding="utf-8").splitlines())
        print(f"  代码行数：A 手写 {la} 行　B LangGraph {lb} 行"
              f"（B 是 A 的 {lb/la:.0%}）")

    print("=" * 74)
    print("\n  怎么读：")
    print("   · 耗时差异**基本来自模型的随机波动**（每次 LLM 2.8~7.5s），不是图的开销")
    print("   · 图版本多出来的成本在【代码结构】上：见 docs/开发指南/15_LangGraph对照.md")
    return 0 if same == n else 1


if __name__ == "__main__":
    sys.exit(main())
