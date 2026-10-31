# -*- coding: utf-8 -*-
"""
"接线"测试（check_agent_wiring.py）—— 补"函数写了但没被调用"这个坑
==================================================================
**为什么单独写这个文件**（2026-10-31 踩到）：

    `check_agent_guard.py` 全绿 ✅，但它测的是 **guard.py 里的函数本身** ✅
    而 `handle()` **根本没调用** `acquire_slot()` 和 `apply_critic_verdict()` ✅ ✗
       → **测试全绿，功能没接** ✅ —— 这比"没写"更糟 ✅

所以这个文件专门验"**主路径真的用了它们**"：

    [1] 并发超限 → `handle()` 直接拒绝（不是"进去以后才发现"）
    [2] Critic 连续两次 WRONG → `handle()` 把状态改成 NEEDS_HUMAN（不是只记一条证据）
    [3] Critic 判 OK → 状态仍是 DONE（**不能因为审核就误拦**）
    [4] `reflect=True` 时**真的多走一步**（步数 4 → 5）
    [5] `reflect=False`（默认）时**步数仍是 4**（不偷偷改变默认行为）

全部用假 LLM + 假工具，秒级 ✅
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select                     # noqa: E402

from hub.agent import guard as g                          # noqa: E402
from hub.agent.handle import handle                       # noqa: E402
from hub.persistence.db import get_session                # noqa: E402
from hub.persistence.models import AgentRun, IssueCluster  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class SeqLLM:
    """按调用顺序返回不同内容（用来自造"判两次"的场景）。"""
    name = "fake"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None) -> str:
        self.calls += 1
        i = min(self.calls - 1, len(self.replies) - 1)
        return self.replies[i]


JUDGE_OK = json.dumps({"tool": "review.draft_reply", "evidence_ids": ["E1"],
                       "recommendation": "回复差评", "confidence": 0.8},
                      ensure_ascii=False)
CRITIC_WRONG = json.dumps({"verdict": "WRONG", "problems": ["动作类型错"],
                           "reason": "改文案不解决问题"}, ensure_ascii=False)
CRITIC_OK = json.dumps({"verdict": "OK", "problems": [], "reason": "一致"},
                       ensure_ascii=False)


def first_cluster():
    with get_session() as s:
        return s.scalars(select(IssueCluster).limit(1)).first()


def cleanup(cid):
    with get_session() as s:
        s.execute(delete(AgentRun).where(AgentRun.cluster_id == cid))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  接线测试：主路径真的用了 guard/critic 吗")
    print("=" * 66)

    c = first_cluster()
    if c is None:
        print("  [!] 库里没有簇，跳过")
        return 1
    cid = str(c.id)

    # ================= [1] 并发超限 → handle() 直接拒绝 =================
    print("\n[1] 并发超限时 handle() 直接拒绝（在调 LLM 之前）")
    # 上限设成 0 → 一定满
    lim0 = g.AgentLimits(max_concurrent=0)
    llm = SeqLLM([JUDGE_OK])
    try:
        handle(cid, llm=llm, limits=lim0)
        check("应该抛 BudgetExceeded", False)
    except g.BudgetExceeded as e:
        check("抛了 BudgetExceeded", True, str(e)[:40])
    check("**没有调模型**（拒绝发生在 LLM 之前）", llm.calls == 0, f"calls={llm.calls}")

    # ================= [2] Critic 连续两次 WRONG → NEEDS_HUMAN =================
    print("\n[2] Critic 连续两次 WRONG → handle() 把状态改成 NEEDS_HUMAN")
    cleanup(cid)
    # 顺序：judge(OK) → critic(WRONG) → judge(OK) → critic(WRONG) → 转人工
    llm = SeqLLM([JUDGE_OK, CRITIC_WRONG, JUDGE_OK, CRITIC_WRONG])
    r = handle(cid, llm=llm, use_critic=True,
               limits=g.AgentLimits(critic_max_reject=2, max_concurrent=99))
    check("status=NEEDS_HUMAN（不是 DONE）", r.status == "NEEDS_HUMAN", r.status)
    check("needs_human=True", r.needs_human is True)
    check("理由里提到 Critic", "Critic" in r.recommendation, r.recommendation[:60])
    check("调了 4 次模型（2 判 + 2 核）", llm.calls >= 4, f"calls={llm.calls}")
    cleanup(cid)

    # ================= [3] Critic 判 OK → 仍是 DONE =================
    print("\n[3] Critic 判 OK → 状态仍是 DONE（审核不能误拦）")
    llm = SeqLLM([JUDGE_OK, CRITIC_OK])
    r = handle(cid, llm=llm, use_critic=True,
               limits=g.AgentLimits(max_concurrent=99))
    check("status=DONE", r.status == "DONE", r.status)
    check("有 E-CRITIC 证据", any(e["ref"] == "E-CRITIC" for e in r.evidence))
    cleanup(cid)

    # ================= [4] reflect=True → 真的多走一步 =================
    print("\n[4] reflect=True 时，NEEDS_HUMAN 会补一步证据（步数 4 → 5）")
    # ⚠️ `max_steps=4`：标准流程已经用掉 3 个**取证步** ✅
    #    要给 reflect 留额度，上限必须 >3 ✅
    #    （第一版测试用了 max_steps=3 → reflect 没额度 → 永远不触发 ✅ 假红）
    judge_need = json.dumps({"needs_human": True, "reason": "证据太少"},
                            ensure_ascii=False)
    llm = SeqLLM([judge_need, JUDGE_OK])
    r = handle(cid, llm=llm, reflect=True,
               limits=g.AgentLimits(max_steps=4, max_concurrent=99))
    check("步数 ≥ 5（多了一步取证）", len(r.steps) >= 5, f"{len(r.steps)} 步")
    # 补的那步必须是 query_product（**只有这一条路**：query_outcome 要 proposal_id ✅）
    check("多出来的那步是 query_product",
          any(s.tool == "query_product" for s in r.steps),
          str([s.tool for s in r.steps]))
    cleanup(cid)

    # ================= [5] reflect=False（默认）→ 仍是 4 步 =================
    print("\n[5] reflect 默认关 → 步数仍是 4（不偷偷改默认行为）")
    llm = SeqLLM([judge_need])
    r = handle(cid, llm=llm, limits=g.AgentLimits(max_concurrent=99))
    check("步数正好 4", len(r.steps) == 4, f"{len(r.steps)} 步")
    check("status=NEEDS_HUMAN", r.status == "NEEDS_HUMAN", r.status)
    cleanup(cid)

    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
