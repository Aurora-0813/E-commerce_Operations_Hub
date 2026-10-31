# -*- coding: utf-8 -*-
"""
agent 主路径测试（check_agent_handle.py）—— 阶段 3.4 / 3.5 的起点
================================================================
**用假模型测，不调真 LLM** —— 所以是秒级、结果稳定、放 quick 组 ✅

验 9 条（1~5 正向、6~9 负向）：
    1. 四步都记了轨迹（①②③ 代码步 + ④ 模型步）
    2. 证据编号连续（E1..En），**每条都带 source/ref**
    3. 模型正常给建议 → status=DONE，tool 在允许集合里
    4. 模型引用的编号**全部有效** → 结论保留
    5. 模型**编造引用** → 那条被列出来（`编造 N 条`），但仍能用有效引用出结论
    6. 模型说 needs_human → status=NEEDS_HUMAN（不硬给结论）
    7. 模型给了**不允许的工具** → NEEDS_HUMAN（不采用）
    8. 模型输出**不是 JSON** → NEEDS_HUMAN（不猜）
    9. 模型**一条有效引用都没有** → NEEDS_HUMAN（没依据就不动手）

为什么负向比正向重要：
    正向只证明"顺利时能跑"；负向才证明"模型乱来时不闯祸" ✅
    —— 而 agent 的风险几乎全在"模型乱来"这一侧 ✅

跑法：python tests/check_agent_handle.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.agent.handle import ALLOWED_TOOLS, handle

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeLLM:
    """假模型：返回预设字符串，并记录收到的 prompt。"""

    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None) -> str:
        self.prompts.append(prompt)
        return self.reply


def first_cluster_id() -> str | None:
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        c = s.scalars(select(IssueCluster).limit(1)).first()
        return str(c.id) if c else None


def main() -> int:
    print("=" * 62)
    print("  agent 主路径（形态 ③）")
    print("=" * 62)

    cid = first_cluster_id()
    if cid is None:
        print("\n[!] 库里没有簇 —— 先跑全链")
        return 1

    # ---------- 1~2 轨迹与证据 ----------
    print("\n[1] 四步轨迹 + [2] 证据编号")
    good = json.dumps({
        "recommendation": "改详情页补充面料说明，并回复该簇差评",
        "tool": "listing.update_desc",
        "evidence_ids": ["E1", "E2", "E3"],
        "confidence": 0.7,
    }, ensure_ascii=False)
    llm = FakeLLM(good)
    r = handle(cid, llm=llm)
    check("有心跳 trace/ctx（不抛异常）", True)
    check("轨迹 4 步", len(r.steps) == 4, f"{len(r.steps)} 步："
          + ",".join(s.tool for s in r.steps))
    check("①②③ 是只读查询", [s.tool for s in r.steps[:3]] ==
          ["query_cluster", "query_reviews", "query_history"])
    check("④ 是模型判断", r.steps[3].tool == "llm.judge", r.steps[3].tool)
    refs = [e["ref"] for e in r.evidence]
    check("证据都有 source/ref", all("source" in e and "ref" in e for e in r.evidence))
    check("有 E1（簇概况）", "E1" in refs, ",".join(refs[:6]))
    # 模型收到的 prompt 里必须带证据编号（否则它没法引用）
    check("prompt 里带了证据编号", "E1" in (llm.prompts[0] if llm.prompts else ""),
          f"{len(llm.prompts[0])} 字符")

    # ---------- 3 正常结论 ----------
    print("\n[3] 正常结论")
    check("status=DONE", r.status == "DONE", r.status)
    check("tool 在允许集合", r.tool in ALLOWED_TOOLS, r.tool)
    check("confidence 在 0~1", 0.0 <= r.confidence <= 1.0, str(r.confidence))

    # ---------- 5 编造引用 ----------
    print("\n[5] 编造引用 → 要列出来但仍能用有效引用出结论")
    mixed = json.dumps({
        "recommendation": "随便改点东西",
        "tool": "review.draft_reply",
        "evidence_ids": ["E1", "E99", "E100"],     # E99/E100 是编的
        "confidence": 0.5,
    }, ensure_ascii=False)
    r2 = handle(cid, llm=FakeLLM(mixed))
    verify = [e for e in r2.evidence if e["ref"] == "E-VERIFY"]
    check("记了校验结论", bool(verify), verify[0]["text"] if verify else "")
    check("列出了编造条数", verify and "编造 2 条" in verify[0]["text"],
          verify[0]["text"] if verify else "")
    check("仍有有效引用 → DONE", r2.status == "DONE", r2.status)

    # ---------- 6 模型要人工 ----------
    print("\n[6] 模型说 needs_human")
    r3 = handle(cid, llm=FakeLLM('{"needs_human": true, "reason": "证据太少"}'))
    check("status=NEEDS_HUMAN", r3.status == "NEEDS_HUMAN", r3.status)
    check("保留了理由", "证据太少" in r3.recommendation, r3.recommendation)

    # ---------- 7 不允许的工具 ----------
    print("\n[7] 模型给了不允许的工具")
    bad_tool = json.dumps({"recommendation": "全站下架", "tool": "product.delete_all",
                           "evidence_ids": ["E1"], "confidence": 0.9}, ensure_ascii=False)
    r4 = handle(cid, llm=FakeLLM(bad_tool))
    check("status=NEEDS_HUMAN", r4.status == "NEEDS_HUMAN", r4.status)
    check("tool 没被采用", r4.tool == "", f"tool={r4.tool!r}")

    # ---------- 8 输出不是 JSON ----------
    print("\n[8] 模型输出不是 JSON")
    r5 = handle(cid, llm=FakeLLM("我觉得应该先看看评价再说"))
    check("status=NEEDS_HUMAN", r5.status == "NEEDS_HUMAN", r5.status)

    # ---------- 9 一条有效引用都没有 ----------
    print("\n[9] 全是编造引用（无有效依据）")
    all_fake = json.dumps({"recommendation": "改价", "tool": "listing.update_desc",
                           "evidence_ids": ["E90", "E91"], "confidence": 0.8},
                          ensure_ascii=False)
    r6 = handle(cid, llm=FakeLLM(all_fake))
    check("status=NEEDS_HUMAN（没依据不动手）", r6.status == "NEEDS_HUMAN", r6.status)

    # ---------- 10 业务规则分歧要显式记录 ----------
    # 实测背景（2026-10-31）：第一次真模型跑，"掉色严重"(quality) 建议"改详情页描述"，
    # 而规则是 review.draft_reply（真质量问题改描述是粉饰）。
    # → 现在 prompt 里给规则；**分歧必须被记下来**，不能悄悄发生 ✅
    print("\n[10] 和业务规则的分歧要显式记录")
    from hub.action.proposer import TOOL_BY_TOPIC
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        row = s.scalars(select(IssueCluster).where(IssueCluster.id == cid)).first()
        cl = s.get(IssueCluster, row.id)
        # 拿这个簇的真实 topic（handle 内部也是这么算的）
        from hub.tools.readonly import _topic_of
        topic = _topic_of(s, row.id) or ""
    rule_tool = TOOL_BY_TOPIC.get(topic)
    other = "listing.update_desc" if rule_tool == "review.draft_reply" else "review.draft_reply"

    print(f"     这个簇 topic={topic}，规则要求 {rule_tool}；故意让它选 {other}")
    diverge = json.dumps({"recommendation": "我不同意规则", "tool": other,
                          "evidence_ids": ["E1"], "confidence": 0.6,
                          "follows_rule": False, "why_not_rule": "这次情况特殊"},
                         ensure_ascii=False)
    r7 = handle(cid, llm=FakeLLM(diverge))
    rule_ev = [e for e in r7.evidence if e["ref"] == "E-RULE"]
    check("记了规则对比", bool(rule_ev), rule_ev[0]["text"] if rule_ev else "")
    if rule_tool:
        check("标成'不一致'", rule_ev and "不一致" in rule_ev[0]["text"],
              rule_ev[0]["text"] if rule_ev else "")
        check("保留了模型的理由", rule_ev and "这次情况特殊" in rule_ev[0]["text"],
              rule_ev[0]["text"] if rule_ev else "")
    check("分歧时仍然 DONE（不因为分歧就拦下）", r7.status == "DONE", r7.status)

    # ---------- 11 prompt 里必须带规则 ----------
    print("\n[11] prompt 里必须包含业务规则")
    fk = FakeLLM(good)
    handle(cid, llm=fk)
    p = fk.prompts[0] if fk.prompts else ""
    check("prompt 里有'业务规则'", "业务规则" in p, f"{len(p)} 字符")
    check("prompt 里点名了该用哪个工具", (rule_tool or "") in p, rule_tool or "")

    print("\n" + "=" * 62)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 62)
        return 1
    print("  全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
