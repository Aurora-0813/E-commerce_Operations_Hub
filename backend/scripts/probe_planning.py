# -*- coding: utf-8 -*-
"""
规划能力探针（probe_planning.py）—— 阶段 1.3
=============================================
**目的：回答一个问题 —— 这个模型能不能做多步规划？**

为什么要在做 agent 之前先测：
    阶段 3 要做的 agent 有「完整 / 半开放 / 受限」三种形态，
    选哪种**必须由数据决定**，不能凭感觉 ✅
    （否则就会变成"先定了要做完整 agent，再去找理由"）

三个取舍（都是为了"测的是模型，不是别的"）：

① **工具是模拟的，不真执行**
   阶段 3.1 的 5 个只读工具还没写；而且这里要测的是"模型会不会选"，
   不是"工具对不对"。所以给 5 个**带真实 schema 的假工具**，返回固定结果。
   → 好处：可重复、不写库、不花钱（除了 LLM 调用本身）
   → 代价：测不出"工具返回真实数据时模型会不会乱"（那属于阶段 3 的活）

② **只在【第一步】有 ground truth**
   期望工具来自 `proposer.TOOL_BY_TOPIC`（代码规则）—— 它是"应该做什么"的标准答案 ✅
   第 2、3 步没有标准答案（取决于第 1 步看到什么），所以只统计"有没有在 3 步内
   达成目标"，不评判中间步骤"对不对"。

③ **给固定 seed**，让不同模型可比（同输入同输出）

指标（三个数，对应路线图 §5.3）：

    ⚠️ **第一版指标设计错了，这里是更正后的定义**：
        v1 按"首步是否等于终态工具"判 → 把「先查簇 → 再查评价 → 再查历史」
        这种**合理探索**判成了 0% ✅ ✗
        （`TOOL_BY_TOPIC` 表达的是"最终该做哪个写动作"，不是"第一步该查什么"）

    v2 改为：
    · 首步只读率 = 第一步是只读查询（不是直接动手）的比例
    · 收尾率     = ≤MAX_STEPS 步内给出结论（done）或调用了终态工具的比例
                   ← **这才是 agent 的真本事：知道什么时候够了**
    · 参数幻觉   = cluster_id 传了编造的字符串（而不是真 UUID）的任务数
    · 上下文     = 平均每步 prompt 字符数（判断会不会撑爆 8k）

跑法：
    python scripts/probe_planning.py                          # 默认本地 qwen2.5:7b
    python scripts/probe_planning.py --model llama3.1:8b      # 换同量级模型
    python scripts/probe_planning.py --base-url https://api.deepseek.com/v1 \
        --api-key sk-xxx --model deepseek-chat                # 跨量级（云端）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from hub.action.proposer import TOOL_BY_TOPIC
from hub.llm import OpenAIText, get_text_llm
from hub.persistence.db import SessionLocal
from hub.persistence.models import ClusterMember, IssueCluster, ReviewTopic

MAX_STEPS = 5          # 最多走几步。
# 为什么从 3 改成 5：
#   实测 7B 的行为是"先查簇 → 再查评价 → 再查历史"，三步刚好用完还没收尾。
#   它并不是"选错工具"，而是"还在收集信息"。3 步测不出它的上限，
#   会把"合理探索"误判成"不会规划" ✅
N_TASKS = 10           # 拿多少个簇来测

# ---------------------------------------------------------------- 假工具
# 5 个只读查询工具（阶段 3.1 会真的实现，这里先给 schema + 固定返回）
TOOLS = {
    "query_cluster": {
        "desc": "查一个簇的概况：名字、条数、平台分布、优先级档位",
        "args": {"cluster_id": "簇 ID（字符串）"},
        "result": ("簇「{title}」共 {n} 条差评，主要集中在 {platform}，"
                   "优先级 {tier}；代表作评价 5 条（已脱敏）。"),
    },
    "query_reviews": {
        "desc": "查某个簇里的代表性评价原文（按距质心排序，最多 5 条）",
        "args": {"cluster_id": "簇 ID", "n": "条数，默认 5"},
        "result": "① “洗一次就掉色了，水都变色” ② “掉毛严重，穿一次满身毛” ③ “和图片色差太大”",
    },
    "query_history": {
        "desc": "查历史上同一类问题的处置方式和结果（有效/有害/不明确各几次）",
        "args": {"topic": "问题类型", "tool": "工具名（可空=全部）"},
        "result": "改描述：3 次里 2 次有效（差评率平均降 10.7pp），1 次撞大促无法判定；回复：1 次有害（+12.0pp）",
    },
    "query_product": {
        "desc": "查某个商品的标题、价格、销量、差评率",
        "args": {"spu_id": "商品 ID"},
        "result": "「森马 纯棉短袖T恤」淘宝 89 元 / 京东 129 元；月销 1200 件；差评率 21.4%",
    },
    "query_outcome": {
        "desc": "查某个提案执行后的复盘结论（DID / 置信区间）",
        "args": {"proposal_id": "提案 ID"},
        "result": "INCONCLUSIVE：窗口与促销重叠（PROMO_INTERFERENCE），无法归因",
    },
}

# 可选的写动作（第 3 步之后才该出现；探针里只用来判断"是否达成"）
WRITE_TOOLS = {
    "listing.update_desc": "改商品详情页描述（L2，需审批）",
    "review.draft_reply": "生成回复草稿（L1）",
    "hitl.create": "转人工处理（L0）",
}

SYSTEM = (
    "你是电商运营的决策助手。你可以通过调用工具来收集信息，最后给出处置建议。\n"
    "每一步只输出一个 JSON，格式：{\"tool\": \"工具名\", \"args\": {...}}\n"
    "如果信息已经足够，输出：{\"done\": true, \"recommendation\": \"一句话建议\"}\n"
    "**重要：不要为了收集而收集。查到能下结论时就立刻给建议（done）。**\n"
    "不要输出 JSON 以外的任何文字。"
)


def tools_prompt() -> str:
    """把工具清单拼成提示词（JSON Schema 风格，但不冗长）。"""
    lines = ["可用工具："]
    for name, t in TOOLS.items():
        args = ", ".join(f"{k}({v})" for k, v in t["args"].items())
        lines.append(f"- {name}：{t['desc']}；参数：{args}")
    lines.append("\n可选的最终动作（用于给建议，不在本轮调用）：")
    for name, d in WRITE_TOOLS.items():
        lines.append(f"- {name}：{d}")
    return "\n".join(lines)


def parse_step(text: str) -> dict | None:
    """从模型输出里抠出第一个 JSON 对象。抠不出来返回 None（算失败）。"""
    text = (text or "").strip()
    # 有些模型会包 ```json ... ``` 或加解释，所以找第一个 { ... }
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def load_tasks(limit: int) -> list[dict]:
    """从库里取若干个簇作为任务（覆盖尽量多的问题类型）。

    ⚠️ **不要用 `issue_cluster.business_domain` 当"问题类型"** ——
    实测它的实际取值是 `女装` / `男装`（**品类**），不是 `quality` / `logistics`（问题类型）。
    这是 `hub/analysis/cluster.py:178-179` 的一个缺陷（把商品类目当成了问题域），
    已记入 `docs/债台账.md` #39。

    正确的问题类型来源：`cluster_member` → `review_topic.topic`
    （M3 产出的封闭枚举，和 `TOOL_BY_TOPIC` 的键是同一套词汇 ✅）。
    """
    with SessionLocal() as s:
        # 每个簇的"主导问题类型"：成员里出现次数最多的 topic
        pairs = s.execute(
            select(ClusterMember.cluster_id, ReviewTopic.topic, func.count())
            .join(ReviewTopic, ReviewTopic.record_id == ClusterMember.record_id)
            .group_by(ClusterMember.cluster_id, ReviewTopic.topic)
        ).all()
        dominant: dict = {}
        for cid, topic, n in pairs:
            if n > dominant.get(cid, ("", 0))[1]:
                dominant[cid] = (topic, n)

        rows = s.scalars(
            select(IssueCluster).order_by(IssueCluster.priority_score.desc()).limit(60)
        ).all()

        tasks, seen_topics = [], set()
        for c in rows:
            topic = dominant.get(c.id, ("", 0))[0]
            if topic not in TOOL_BY_TOPIC:
                continue
            # 先保证覆盖不同 topic，凑够了再补
            if topic in seen_topics and len(tasks) < len(TOOL_BY_TOPIC):
                continue
            seen_topics.add(topic)
            tasks.append({
                "cluster_id": str(c.id),
                "title": c.label or "(未命名)",
                "topic": topic,
                "tier": c.priority_tier or "P?",
                "n": c.review_count or 0,
                "platform": "/".join(c.platforms or []) or "both",
                "expect": TOOL_BY_TOPIC[topic],       # ground truth：该调哪个写工具
            })
            if len(tasks) >= limit:
                break

        if not tasks and rows:
            from collections import Counter
            actual = Counter(dominant.get(c.id, ("<无>", 0))[0] for c in rows)
            print("\n[X] 从 cluster_member→review_topic 也推不出 TOOL_BY_TOPIC 的键。")
            print(f"    TOOL_BY_TOPIC 的键 : {sorted(TOOL_BY_TOPIC)}")
            print(f"    簇成员里实际出现的   : {dict(actual)}")
            print("    → 说明 review_topic.topic 的词汇表和 TOOL_BY_TOPIC 不一致，需核对。\n")
    return tasks


def run_one(llm: OpenAIText, task: dict) -> dict:
    """跑一个任务：最多 MAX_STEPS 步，记录轨迹。"""
    history: list[str] = []
    steps: list[dict] = []
    prompt_chars = 0

    for i in range(MAX_STEPS):
        prompt = (
            f"{tools_prompt()}\n\n"
            f"任务：处理问题簇「{task['title']}」。\n"
            f"簇 ID = {task['cluster_id']}（**查这个簇时 cluster_id 必须用这个值**）\n"
            f"问题类型 = {task['topic']}，{task['n']} 条差评，优先级 {task['tier']}，"
            f"平台 {task['platform']}。\n"
            f"已收集的信息：\n" + ("\n".join(history) if history else "（还没有）") +
            f"\n\n现在是第 {i + 1} 步（最多 {MAX_STEPS} 步），请决定下一步。"
        )
        prompt_chars += len(prompt)
        try:
            out = llm.chat(prompt, system=SYSTEM, temperature=0.0, max_tokens=200)
        except Exception as e:                       # 云端可能超时/限流
            steps.append({"step": i + 1, "error": str(e)[:120]})
            break

        step = parse_step(out)
        if step is None:
            steps.append({"step": i + 1, "raw": out[:80], "parse": "FAIL"})
            continue

        if step.get("done"):
            steps.append({"step": i + 1, "done": True,
                          "recommendation": str(step.get("recommendation", ""))[:80]})
            break

        name = step.get("tool") or ""
        steps.append({"step": i + 1, "tool": name,
                      "args": step.get("args") or {},
                      "known": name in TOOLS or name in WRITE_TOOLS})
        # 回灌：用假结果模拟"看到了什么"
        if name in TOOLS:
            t = TOOLS[name]
            history.append(f"- 第 {i + 1} 步 {name} → " + t["result"].format(
                title=task["title"], n=task["n"], platform=task["platform"], tier=task["tier"]))
        elif name in WRITE_TOOLS:
            history.append(f"- 第 {i + 1} 步 决定执行 {name}（写动作）")
            break                                    # 写动作出现就算收尾
        else:
            history.append(f"- 第 {i + 1} 步 调用了不存在的工具 {name!r}（失败）")

    chosen = [s.get("tool") for s in steps if s.get("tool")]
    # ---------------------------------------------------------------
    # 指标定义（第一版设计错了，这里重写，理由见文件头"指标"一节的更正）
    # ---------------------------------------------------------------
    # ① 首步是否只读：第一步就去调写动作 = 没查清就想动手（危险）
    first_readonly = bool(chosen and chosen[0] in TOOLS)
    # ② 是否收尾：给结论（done）或调用了期望的写动作
    closed = any(s.get("done") for s in steps) or (task["expect"] in chosen)
    # ③ 参数幻觉：参数值不是"从工具返回里拿的"，而是模型编的。
    #    第一版只查了 cluster_id，漏掉了 spu_id / proposal_id ——
    #    而实测恰恰是这两个在编（'目标商品ID' / '待定' / 自己拼 proposal_id）。
    #    判据：ID 类参数必须是"像 ID 的串"（字母数字下划线短横，长度≥8），
    #    出现中文描述即视为编造。
    id_like = re.compile(r"^[0-9a-zA-Z_\-]{8,}$")
    bad_args = 0
    for s in steps:
        args = s.get("args") or {}
        for key, val in args.items():
            vs = str(val)
            if key == "cluster_id":
                if vs and vs != task["cluster_id"]:
                    bad_args += 1
            elif key in ("spu_id", "proposal_id"):
                # 空串是"没给"，不算编造；非空且不像 ID 就算编造
                if vs and not id_like.match(vs):
                    bad_args += 1
    # ④ 调了不存在的工具
    fake_tool = any(s.get("tool") and not s.get("known") for s in steps)

    return {
        "task": task,
        "steps": steps,
        "n_steps": len(steps),
        "first_readonly": first_readonly,
        "closed": closed,
        "bad_args": bad_args,
        "fake_tool": fake_tool,
        "prompt_chars": prompt_chars,
        "avg_prompt": prompt_chars // max(1, len(steps)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None, help="默认用 .env 配置")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=N_TASKS)
    a = ap.parse_args()

    llm = get_text_llm()
    if a.base_url or a.model or a.api_key:
        llm = OpenAIText(base_url=a.base_url or llm.base_url,
                         model=a.model or llm.model,
                         api_key=a.api_key or llm.api_key)

    print("=" * 62)
    print(f"  规划能力探针　model={llm.model}　base={llm.base_url}")
    print(f"  最多 {MAX_STEPS} 步 × {a.limit} 个任务")
    print("=" * 62)

    tasks = load_tasks(a.limit)
    if not tasks:
        print("[X] 库里没有可用于测试的簇（先跑 setup/全链）")
        return 1

    results = []
    for i, t in enumerate(tasks, 1):
        r = run_one(llm, t)
        results.append(r)
        flag = "✅" if r["closed"] else "❌"
        print(f"\n[{i}/{len(tasks)}] {flag} {t['title'][:26]}")
        print(f"     类型={t['topic']}  期望终态={t['expect']}  簇ID={t['cluster_id'][:8]}…")
        for s in r["steps"]:
            if s.get("parse") == "FAIL":
                print(f"     第{s['step']}步 解析失败：{s['raw']!r}")
            elif "error" in s:
                print(f"     第{s['step']}步 报错：{s['error']}")
            elif s.get("done"):
                print(f"     第{s['step']}步 收尾：{s['recommendation']}")
            else:
                print(f"     第{s['step']}步 {s['tool']}  参数={s['args']}")

    n = len(results)
    first_ro = sum(r["first_readonly"] for r in results) / n
    closed = sum(r["closed"] for r in results) / n
    halluc = sum(1 for r in results if r["bad_args"] > 0)
    fake = sum(1 for r in results if r["fake_tool"])
    avg_prompt = sum(r["avg_prompt"] for r in results) // n
    avg_steps = sum(r["n_steps"] for r in results) / n

    print("\n" + "=" * 62)
    print(f"  首步只读率   {first_ro:.0%}　（先查再动手 = 合理）")
    print(f"  收尾率      {closed:.0%}　（{sum(r['closed'] for r in results)}/{n}，"
          f"≤{MAX_STEPS} 步内给出结论或调了终态工具）")
    print(f"  参数幻觉     {halluc}/{n} 个任务里编造了 ID 类参数（spu_id / proposal_id / cluster_id）")
    print(f"  调不存在的工具 {fake}/{n}")
    print(f"  平均步数     {avg_steps:.1f}")
    print(f"  平均每步 prompt  {avg_prompt} 字符（≈ {avg_prompt // 2} token，7B 上下文约 8k）")
    print("-" * 62)

    # 判定标准（改过一次：第一版按"首步是否等于终态工具"判，把合理的探索
    # 算成了错。现在按【收尾率】判 —— 那才是 agent 的真本事）
    if closed >= 0.7:
        verdict = "① 完整 agent（LLM 自由规划）"
    elif closed >= 0.5:
        verdict = "② 半开放 agent（LLM 选工具，代码限步数）"
    else:
        verdict = "③ 受限 agent（固定步骤，LLM 只填参数）"
    print(f"  判定：{verdict}")
    if halluc:
        print(f"  ⚠️ 有 {halluc} 个任务编造了参数 —— 上 agent 前必须加参数校验（网关已有 schema 校验）")
    if fake:
        print(f"  ⚠️ 有 {fake} 个任务调了不存在的工具 —— 同上，网关会拦，但要记录")
    print("=" * 62)
    print("\n把上面的数字贴到 docs/开发指南/14_Agent规划能力实测.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
