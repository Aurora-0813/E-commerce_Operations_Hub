# -*- coding: utf-8 -*-
"""
agent 运行约束测试（check_agent_guard.py）—— 补 7.3/7.4 的六项
============================================================
**不调真 LLM**（用假模型）→ 秒级，进 quick 组 ✅

验 6 组，对应路线图里我第一版没做的六项：
    [1] 步数上限（max_steps）
    [2] token 预算（超了 → FAILED + E-GUARD）
    [3] 超时（deadline 检查）
    [4] 并发上限（max_concurrent）
    [5] 提交幂等（重复提交返回已有 task_id，不新建）
    [6] Critic 连续两次不通过 → 转人工（escalate）

每组的预期都写清"为什么是这条"，不然后人改了就不知道原意 ✅
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.agent import guard as g                     # noqa: E402
from hub.agent.handle import HandleResult, Step, save  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None) -> str:
        self.calls += 1
        return self.reply


def good_judge(tool="review.draft_reply") -> str:
    return json.dumps({"tool": tool, "evidence_ids": ["E1"],
                       "recommendation": "回复差评", "confidence": 0.8},
                      ensure_ascii=False)


def first_cluster():
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        c = s.scalars(select(IssueCluster).limit(1)).first()
        return c


def make_result(*, status="DONE", tool="review.draft_reply", topic="quality",
                steps=4, tokens_in=0, tokens_out=0) -> HandleResult:
    r = HandleResult(cluster_id="x", goal="g", topic=topic)
    r.status, r.tool, r.recommendation, r.confidence = status, tool, "回复差评", 0.8
    r.tokens_in, r.tokens_out = tokens_in, tokens_out
    r.steps = [Step(idx=i + 1, tool=t, args={}, ok=True)
               for i, t in enumerate(["query_cluster", "query_reviews",
                                      "query_history", "llm.judge"][:steps])]
    r.evidence = [{"source": "query_cluster", "ref": "E1", "text": "簇"}]
    return r


def main() -> int:
    print("=" * 64)
    print("  agent 运行约束（guard）—— 7.3/7.4 的六项")
    print("=" * 64)

    L = g.AgentLimits

    # ---------- [1] 步数上限 ----------
    print("\n[1] 步数上限（max_steps=3）")
    lim = L(max_steps=3)
    r3 = make_result(steps=3)                       # 3 步 = 用满了
    check("用满步数后不再 reflect", g.should_reflect(
        make_result(status="NEEDS_HUMAN", steps=3), lim) is False)
    check("没到上限且状态是 NEEDS_HUMAN → 可以 reflect",
          g.should_reflect(make_result(status="NEEDS_HUMAN", steps=2), lim) is True)
    check("DONE 状态不 reflect（没缺证据）",
          g.should_reflect(make_result(status="DONE"), lim) is False)

    # ---------- [2] token 预算 ----------
    print("\n[2] token 预算（20000）")
    lim2 = L(token_budget=20000)
    check("没超", g.token_exceeded(10000, 5000, lim2) is False)
    check("超了（in+out 一起算）", g.token_exceeded(18000, 5000, lim2) is True)
    check("刚好等于不算超", g.token_exceeded(15000, 5000, lim2) is False)

    # ---------- [3] 超时 ----------
    print("\n[3] 超时（5 分钟，按步检查）")
    lim3 = L(timeout_s=300)
    try:
        g.check_deadline(time.time(), lim3)
        check("刚开始不超时", True)
    except g.BudgetExceeded:
        check("刚开始不超时", False)
    try:
        g.check_deadline(time.time() - 400, lim3)   # 假装已经跑了 400 秒
        check("400 秒后应该超时", False)
    except g.BudgetExceeded as e:
        check("400 秒后报超时", True, str(e)[:40])

    # ---------- [4] 并发上限 ----------
    print("\n[4] 并发上限（max_concurrent=2）")
    lim4 = L(max_concurrent=2)
    n_running = g.count_running()
    check(f"能数到当前 RUNNING 数（{n_running}）", isinstance(n_running, int))
    # 造一个"已满"的情形：把上限设成 n_running，再申请就该被拒
    lim_full = L(max_concurrent=max(n_running, 0))
    try:
        g.acquire_slot(lim_full)
        check("已满时应该抛 BudgetExceeded", False)
    except g.BudgetExceeded as e:
        check("已满时抛 BudgetExceeded", True, str(e)[:40])
    # 上限调大就能占
    try:
        g.acquire_slot(L(max_concurrent=n_running + 5))
        check("有余量时能占到", True)
    except g.BudgetExceeded:
        check("有余量时能占到", False)

    # ---------- [6] Critic 连续两次不通过 → 转人工 ----------
    print("\n[6] Critic 连续两次不通过 → 转人工")
    lim6 = L(critic_max_reject=2)
    check("第 1 次 WRONG → continue", g.apply_critic_verdict("WRONG", 1, lim6) == "continue")
    check("第 2 次 WRONG → escalate（转人工）",
          g.apply_critic_verdict("WRONG", 2, lim6) == "escalate")
    check("OK → pass", g.apply_critic_verdict("OK", 0, lim6) == "pass")
    check("WEAK 不算不通过 → pass", g.apply_critic_verdict("WEAK", 1, lim6) == "pass")
    check("SKIP → pass", g.apply_critic_verdict("SKIP", 0, lim6) == "pass")

    # ---------- [5] 提交幂等（要真库）----------
    print("\n[5] 提交幂等：重复提交返回已有 task_id")
    c = first_cluster()
    if c is None:
        print("  [!] 库里没有簇，跳过")
    else:
        r = make_result(status="NEEDS_HUMAN")
        r.cluster_id = str(c.id)
        # 先清掉这个簇的未完成任务，保证从干净状态开始
        from hub.persistence.db import get_session
        from hub.persistence.models import AgentRun
        from sqlalchemy import delete
        with get_session() as s:
            s.execute(delete(AgentRun).where(
                AgentRun.cluster_id == c.id,
                AgentRun.status.in_(("RUNNING", "NEEDS_HUMAN"))))
            s.commit()

        id1 = save(r)
        id2 = save(r)                                # 同簇再提交一次
        check("两次返回同一个 id（幂等）", id1 == id2, f"{str(id1)[:8]} / {str(id2)[:8]}")
        with get_session() as s:
            n = s.query(AgentRun).filter(AgentRun.cluster_id == c.id).count()
        check("库里只有 1 条（没新建）", n == 1, f"{n} 条")

        # 清掉测试造的
        with get_session() as s:
            s.execute(delete(AgentRun).where(AgentRun.cluster_id == c.id))
            s.commit()

    print("\n" + "=" * 64)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 64)
        return 1
    print("  全部通过")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
