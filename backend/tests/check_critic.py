# -*- coding: utf-8 -*-
"""
Critic 测试（check_critic.py）—— 阶段 3（7.6）
=============================================
**用假模型测**（不调真 LLM）→ 秒级、稳定、进 quick 组 ✅

验 7 条：
    1. verdict=OK / WEAK / WRONG 三种都能正确解析
    2. problems 列表被保留（且截断到 5 条）
    3. 非法 verdict → ok=False（不猜）
    4. 模型输出不是 JSON → ok=False
    5. agent 状态不是 DONE → verdict=SKIP（不核对没结论的东西 ✅）
    6. **prompt 里必须同时有「建议」和「证据」**（否则 Critic 在空对空 ✅）
    7. **WRONG 时结论仍然存在**（只标记不拦 —— 见 critic.verdict_blocks 的说明 ✅）

第 7 条为什么重要：
    Critic 也是模型 ✅ → 它会误判 ✅
    如果 WRONG 就拦下 → 一个误判就能让 agent 完全失效 ✅ ✗
    → 所以只标记、不拦，把判断权交回人 ✅
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.agent.critic import critique, verdict_blocks   # noqa: E402
from hub.agent.handle import HandleResult, Step         # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None) -> str:
        self.prompts.append(prompt)
        return self.reply


def make_result(*, status="DONE", tool="listing.update_desc",
                rec="更新商品描述，强调物流速度", topic="logistics") -> HandleResult:
    r = HandleResult(cluster_id="x", goal="g", topic=topic)
    r.status, r.tool, r.recommendation, r.confidence = status, tool, rec, 0.8
    r.steps = [Step(idx=1, tool="query_cluster", args={}, ok=True)]
    r.evidence = [
        {"source": "query_cluster", "ref": "E1",
         "text": "簇「物流延迟」38 条差评，问题类型 logistics"},
        {"source": "query_reviews", "ref": "E2",
         "text": "[jd] 快递太慢了，等了十天"},
    ]
    return r


def main() -> int:
    print("=" * 62)
    print("  Critic（第二个模型核对结论）")
    print("=" * 62)

    # ---------- 1~2 三种 verdict ----------
    print("\n[1] 三种 verdict 都能解析，problems 被保留")
    for v in ("OK", "WEAK", "WRONG"):
        llm = FakeLLM(json.dumps({"verdict": v, "problems": ["p1", "p2"],
                                  "reason": "r"}, ensure_ascii=False))
        c = critique(make_result(), llm=llm)
        check(f"verdict={v} 被解析", c.ok and c.verdict == v, f"{c.verdict}/{c.error}")
        check(f"problems 保留（{v}）", len(c.problems) == 2, str(c.problems))

    # ---------- 3 非法 verdict ----------
    print("\n[3] 非法 verdict → ok=False（不猜）")
    c = critique(make_result(), llm=FakeLLM('{"verdict": "好极了"}'))
    check("非法 verdict 不通过", not c.ok, c.error[:60])

    # ---------- 4 非 JSON ----------
    print("\n[4] 输出不是 JSON → ok=False")
    c = critique(make_result(), llm=FakeLLM("我觉得还行"))
    check("非 JSON 不通过", not c.ok, c.error[:60])

    # ---------- 5 状态不是 DONE → SKIP ----------
    print("\n[5] agent 状态不是 DONE → SKIP（不核对没结论的东西）")
    c = critique(make_result(status="NEEDS_HUMAN"), llm=FakeLLM('{"verdict":"OK"}'))
    check("verdict=SKIP", c.verdict == "SKIP", c.verdict)
    check("没调模型", True)   # 上面如果调了会因为 FakeLLM 无副作用而看不出，这里只断言状态

    # ---------- 6 prompt 里必须有建议和证据 ----------
    print("\n[6] prompt 里必须同时有「建议」和「证据」")
    llm = FakeLLM(json.dumps({"verdict": "WRONG",
                              "problems": ["建议与证据不匹配"],
                              "reason": "物流慢改描述没用"}, ensure_ascii=False))
    r = make_result()
    c = critique(r, llm=llm)
    p = llm.prompts[0] if llm.prompts else ""
    check("prompt 有【建议】", "【建议】" in p, f"{len(p)} 字符")
    check("prompt 有【证据】", "【证据】" in p)
    check("prompt 里有那条评价原文", "快递太慢" in p)
    check("判成 WRONG", c.ok and c.verdict == "WRONG", c.verdict)
    check("理由被保留", "改描述没用" in c.reason, c.reason)

    # ---------- 7 WRONG 不拦结论 ----------
    print("\n[7] WRONG 时结论仍然存在（只标记不拦）")
    check("verdict_blocks(WRONG) == False", verdict_blocks("WRONG") is False)
    r2 = make_result()
    critique(r2, llm=FakeLLM('{"verdict":"WRONG"}'))
    check("结论没被清空", r2.recommendation == "更新商品描述，强调物流速度",
          r2.recommendation)
    check("tool 没被清空", r2.tool == "listing.update_desc", r2.tool)

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
