# -*- coding: utf-8 -*-
"""
agent 轨迹评测（evaluate_agent.py）—— 阶段 3.5
==============================================
**回答一个问题：形态 ③ 的 agent 到底"好不好用"，好在哪、差在哪。**

为什么要批量跑而不是看一条：
    单条 agent_run 只能说明"这次跑通了" ✅
    要回答"它稳不稳、和规则差多少、编不编引用"，必须**一批** ✅

量四件事（对应路线图 §五 的轨迹评测）：

    ① **和业务规则的分歧率** ← **核心指标，但不是越低越好**
       一致率高 = agent 没提供新信息（规则已经够了）
       一致率低 = 要么规则错了，要么模型不行
       → 所以真正要看的是**分歧的那些，理由站不站得住** ✅

    ② **引用质量**：引用了多少条 / 有效多少 / **编造多少**
       ← 实测第一次真机跑是"引用 2 条有效 2 条"，这里要在大样本上确认

    ③ **步数与耗时**：应当是"3 个只读步（毫秒级）+ 1 个模型步（秒级）"
       ← 如果哪次只读步也慢，说明数据库有慢查询 ✅

    ④ **status 分布**：DONE / NEEDS_HUMAN / FAILED
       ← NEEDS_HUMAN 不是失败，是"证据不足或模型乱来，交给人" ✅

**不落库**（`save()` 不调用）：
    `agent_run` 上有部分唯一索引 "同一簇只允许一个 RUNNING/NEEDS_HUMAN" ✅
    评测要**反复**跑同一批簇 → 落库会撞索引 ✅
    （那个索引本身是对的：防止两个运营同时点"让 AI 出方案" ✅
      真正该补的是 `save()` 遇到已有未完成任务时**返回它**而不是报错 —— 记进台账）

跑法：
    python scripts/evaluate_agent.py                 # 默认 10 个簇（P0/P1 优先）
    python scripts/evaluate_agent.py --limit 22      # 全量
    python scripts/evaluate_agent.py --limit 5 --tier P0
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from hub.action.proposer import TOOL_BY_TOPIC
from hub.agent.handle import handle
from hub.persistence.db import get_session
from hub.persistence.models import IssueCluster
from hub.tools.readonly import _topic_of


def pick_clusters(limit: int, tier: str | None) -> list[dict]:
    """挑要测的簇：P0/P1 优先（影响最大的先测）。"""
    with get_session() as s:
        q = select(IssueCluster).order_by(
            IssueCluster.priority_tier.asc().nullslast(),
            IssueCluster.priority_score.desc().nullslast(),
        )
        if tier:
            q = q.where(IssueCluster.priority_tier == tier)
        rows = s.scalars(q.limit(limit * 3)).all()
        out = []
        for c in rows:
            out.append({
                "id": str(c.id), "label": c.label, "tier": c.priority_tier,
                "n": c.review_count, "topic": _topic_of(s, c.id) or "",
            })
            if len(out) >= limit:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--tier", default=None, help="只测某个优先级，如 P0")
    ap.add_argument("--no-rule", action="store_true",
                    help="对照实验：不把业务规则注入 prompt（测模型自己的判断）")
    # ---- 跨量级对照（阶段 1.3 的"云端"那一组）----
    # 为什么不改 .env：改 .env 会影响**所有**命令（包括别的测试）✅
    # 这里只在**这次运行内**造一个客户端 ✅ → 跑完即失效 ✅
    ap.add_argument("--base-url", default=None, help="云端 OpenAI 兼容 base（含 /v1）")
    ap.add_argument("--api-key", default=None, help="云端 key")
    ap.add_argument("--model", default=None, help="云端模型名")
    ap.add_argument("--allow-cloud-text", action="store_true",
                    help="**放行评价原文上云**（默认被 CloudGuard 拒）；只用于对照实验")
    a = ap.parse_args()

    # ---- 造客户端（云端时显式开 allow_text + 打印警告）----
    llm = None
    if a.base_url or a.model:
        from hub.llm import OpenAIText
        llm = OpenAIText(
            base_url=a.base_url or "http://localhost:11434/v1",
            model=a.model or "qwen2.5:7b",
            api_key=a.api_key,
            cloud_policy=dict(
                # ⚠️ 这是**刻意的例外**：本次实验要把评价原文发到云端 ✅
                #    生产环境这个开关必须保持 False（见 docs/改进路线图.md §二）
                allow_text=bool(a.allow_cloud_text),
                allow_aggregate=True,
                do_redact=True,
            ),
        )
        if a.allow_cloud_text:
            print("=" * 70)
            print("  ⚠️ 本次会把**评价原文**发送到云端（仅用于对照实验）")
            print(f"     目标：{llm.base_url}　模型：{llm.model}")
            print("     生产环境这个开关必须关闭 —— 见 docs/改进路线图.md §二")
            print("=" * 70)

    clusters = pick_clusters(a.limit, a.tier)
    if not clusters:
        print("没有可测的簇（先跑全链）。")
        return 1

    group = "无规则（模型自判）" if a.no_rule else "有规则（生产形态）"
    print("=" * 70)
    print(f"  agent 轨迹评测　{len(clusters)} 个簇　组别：{group}")
    if llm is not None:
        print(f"  模型：{llm.model}　（{llm.base_url}）")
    print("=" * 70)

    rows = []
    for i, c in enumerate(clusters, 1):
        rule_tool = TOOL_BY_TOPIC.get(c["topic"], "")
        try:
            r = handle(c["id"], use_rule=not a.no_rule, llm=llm)
        except Exception as e:                     # noqa: BLE001
            print(f"\n[{i}/{len(clusters)}] ❌ {c['label'][:20]}　异常：{type(e).__name__}: {e}")
            rows.append({"c": c, "rule": rule_tool, "status": "EXCEPTION",
                         "tool": "", "cited": 0, "valid": 0, "fake": 0,
                         "llm_ms": 0, "db_ms": 0})
            continue

        # ---- 从证据里把校验结果抠出来 ----
        verify = next((e["text"] for e in r.evidence if e["ref"] == "E-VERIFY"), "")
        import re
        m = re.search(r"引用了 (\d+) 条证据，有效 (\d+) 条", verify)
        cited = int(m.group(1)) if m else 0
        valid = int(m.group(2)) if m else 0
        fake = cited - valid
        # ---- 耗时拆分：只读步 vs 模型步 ----
        db_ms = sum(s.ms for s in r.steps if s.tool.startswith("query_"))
        llm_ms = sum(s.ms for s in r.steps if s.tool == "llm.judge")

        flag = "✅" if r.status == "DONE" else ("△" if r.status == "NEEDS_HUMAN" else "❌")
        same = (r.tool == rule_tool) if rule_tool else None
        mark = "同" if same else ("异" if same is False else "—")
        print(f"\n[{i}/{len(clusters)}] {flag} {c['label'][:22]}（{c['tier']}，{c['n']} 条）")
        print(f"     规则={rule_tool or '(无)'}　模型={r.tool or '(无)'}　[{mark}]"
              f"　status={r.status}")
        print(f"     引用 {cited} 条（有效 {valid}、编造 {fake}）"
              f"　只读步 {db_ms}ms　模型步 {llm_ms}ms")
        # ⚠️ **建议原文必须记下来**（2026-10-31 补的缺口）：
        #    B 组（无规则）实测 22/22 全选 listing.update_desc，
        #    但当时没记建议原文 → "为什么全选它"只能推测 ✅
        #    要验证"是不是'默认生成改描述的泛化建议 → 工具跟着建议走'"，必须看原文 ✅
        print(f"     建议：{r.recommendation[:100]}")
        # ⚠️ 失败时必须打原因（2026-10-31 踩到）：
        #    第一次跑云端时 key 粘成了占位符 → 22 个全 FAILED，模型步 0~18ms
        #    但脚本只打了"建议：（空）"→ **看不出为什么失败** ✅ ✗
        if r.status == "FAILED" and r.error:
            print(f"     ❌ 失败原因：{r.error[:160]}")
        if r.needs_human:
            print(f"     需要人工：{r.recommendation[:80]}")
        rule_ev = next((e["text"] for e in r.evidence if e["ref"] == "E-RULE"), "")
        if rule_ev and "不一致" in rule_ev:
            print(f"     {rule_ev[:110]}")

        rows.append({"c": c, "rule": rule_tool, "status": r.status, "tool": r.tool,
                     "cited": cited, "valid": valid, "fake": fake,
                     "llm_ms": llm_ms, "db_ms": db_ms,
                     "rec": r.recommendation or ""})

    # ---------------- 汇总 ----------------
    n = len(rows)
    done = [r for r in rows if r["status"] == "DONE"]
    human = [r for r in rows if r["status"] == "NEEDS_HUMAN"]
    bad = [r for r in rows if r["status"] in ("FAILED", "EXCEPTION")]
    compared = [r for r in rows if r["rule"]]
    same = [r for r in compared if r["tool"] == r["rule"]]

    total_cited = sum(r["cited"] for r in rows)
    total_fake = sum(r["fake"] for r in rows)
    avg_llm = sum(r["llm_ms"] for r in rows) // max(1, n)
    avg_db = sum(r["db_ms"] for r in rows) // max(1, n)

    print("\n" + "=" * 70)
    print(f"  status：DONE {len(done)}（{len(done)/n:.0%}）　"
          f"NEEDS_HUMAN {len(human)}（{len(human)/n:.0%}）　异常 {len(bad)}")
    if compared:
        print(f"  与规则一致：{len(same)}/{len(compared)}（{len(same)/len(compared):.0%}）"
              f"　← **分歧率 {1 - len(same)/len(compared):.0%}**")
    print(f"  引用总数 {total_cited}　**编造 {total_fake}**"
          f"（{total_fake/total_cited:.0%}）" if total_cited else "  引用：无")
    print(f"  平均耗时：只读步 {avg_db}ms　模型步 {avg_llm}ms"
          f"（后者占 {avg_llm/(avg_db+avg_llm or 1):.1%}）")
    print("-" * 70)

    # ---------------- 结论（按阈值给判断，不吹） ----------------
    print("  怎么读这些数：")
    if total_cited:
        print(f"   · 引用编造率 {total_fake/total_cited:.0%}："
              + ("✅ 模型引用基本可信" if total_fake == 0
                 else "⚠️ 有编造 → `E-VERIFY` 机制是必要的"))

    # ---------------- 建议原文的分布（补的缺口） ----------------
    # 要验证的假设（2026-10-31，B 组 22/22 全选 update_desc）：
    #   是不是「没规则 → 默认生成一个『改描述』的泛化建议 → 工具跟着建议走」？
    # 判据：建议原文里出现「描述/详情页/说明」的，和选 update_desc 的是不是同一批 ✅
    # ⚠️ 中文文案里的引号一律用「」，别用 ASCII 的 " ——会把字符串提前闭合 ✅
    print("\n  【建议原文的倾向】（验证「工具是不是跟着建议走」）")
    kw_desc = ("描述", "详情页", "详情", "说明", "标注", "补充")
    n_desc = sum(1 for r in rows if any(k in r.get("rec", "") for k in kw_desc))
    n_tool_desc = sum(1 for r in rows if r["tool"] == "listing.update_desc")
    print(f"   · 建议里提到「描述/详情页」的：{n_desc}/{n}（{n_desc/n:.0%}）")
    print(f"   · 实际选了 listing.update_desc 的：{n_tool_desc}/{n}（{n_tool_desc/n:.0%}）")
    if n_desc and n_tool_desc:
        overlap = sum(1 for r in rows
                      if any(k in r.get("rec", "") for k in kw_desc)
                      and r["tool"] == "listing.update_desc")
        print(f"   · 两者重合：{overlap}/{min(n_desc, n_tool_desc)} "
              f"（{overlap/min(n_desc, n_tool_desc):.0%}）")
        if overlap == min(n_desc, n_tool_desc) and n_desc == n_tool_desc:
            print("   → **完全重合**：可以认为「工具是跟着建议走的」 ✅")
            print("     （即：模型先写一个泛化的『改描述』建议，再挑对应的工具）")
        else:
            print("   → 不完全重合：说明还有别的因素影响工具选择，需要看具体样本")
    print("\n  【前 3 条建议原文】")
    for r in rows[:3]:
        print(f"   · {r['c']['label'][:16]}：{r.get('rec', '')[:70]}")

    if a.no_rule:
        d = (1 - len(same) / len(compared)) if compared else 0.0
        print(f"\n   · **对照组（没给规则）**：分歧率 {d:.0%} "
              "= **模型自己的判断和规则差多少** ← 这才是'agent 提供多少新信息'")
        print("   · 和【有规则组】比：一致率的差异 = 规则把判断拉回来了多少")
    else:
        print("\n   · **生产形态（给了规则）**：一致率只说明'它会不会遵守规则'")
        print("   · **别只看这一组** —— 跑 `--no-rule` 那组才是'模型自己的判断'")
        print("   · 若两组一致率都高 → 规则与常识一致，agent 的价值在'给依据/可追溯'")
        print("   · 若无规则组分歧明显更大 → **规则确实在起作用**，不是废话")
    print("=" * 70)
    print("\n下一步：把这份数字贴进 docs/开发指南/14_Agent规划能力实测.md 的 §9。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
