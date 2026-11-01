# -*- coding: utf-8 -*-
"""
日报测试（check_brief.py）—— 阶段 3.6
====================================
路线图 §九 的验收：
    "注入一个假数字 → **该条被丢弃、其余保留**" ✅

验 7 条：
    [1] 数字校验：**真实存在的数字** → 通过 ✅
    [2] 数字校验：**编造的数字**（输入里没有 12.34%）→ 被丢弃 ✅
    　　← **这是本阶段的核心：反幻觉** ✅
    [3] 允许单位后缀（"123 条" 里的 123 能在输入里找到 ✅）
    [4] 丢弃 ≤1 条 → 保留其余，**不重写** ✅
    [5] 丢弃 >1 条 → **重写一次**（attempts == 2 ✅）
    [6] 重写后仍不足 3 条 → **退化成原始指标表**（fallback_table=True ✅）
    [7] 落库到 `analysis_run`，`stats.module="brief"`（**不新建表** ✅）

全部用假 LLM，秒级 ✅
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeLLM:
    name = "fake"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None):
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


# 固定的假输入：只有 123 / 45 / 0.05 / 7 这几个数 ✅
# ⚠️ **不要用"失败 N 次"这种词**（2026-11-01 踩到）：
#    `run_tests.py` 的 FAIL_PAT 里是 `失败 [1-9]\d*\b`
#    → 测试自己的**数据**会匹配到失败模式 → 明明全过却判失败 ✅ ✗
#    （这和 check_normalize 里 `❌` 当测试数据是同一类坑 ✅）
INPUTS = {"status": {"簇数": 123, "死信": 0},
          "health": {"兜底率": 0.05, "异常数": 7},
          "change": {"今天评价数": 45, "昨天评价数": 40}}


def main() -> int:
    print("=" * 66)
    print("  AI 日报（阶段 3.6）—— 数字必须能在输入里找到")
    print("=" * 66)

    from hub.brief import _flatten_numbers, generate, latest, save, verify_line

    allowed: set[str] = set()
    _flatten_numbers(INPUTS, allowed)

    # ---------- [1] 真数字通过 ----------
    print("\n[1] 输入里存在的数字 → 通过")
    ok, bad = verify_line("📊 今天 123 个簇，异常 7 次", allowed)
    check("通过", ok, f"bad={bad}")
    ok, bad = verify_line("⚠️ 兜底率 0.05", allowed)
    check("小数也认", ok, f"bad={bad}")

    # ---------- [2] 编造的数字被丢弃 ----------
    print("\n[2] **编造的数字 → 被丢弃**（反幻觉核心）")
    ok, bad = verify_line("🎉 差评率下降了 12.34%", allowed)
    check("不通过", not ok, f"bad={bad}")
    check("找出了编造的数字", "12.34" in bad, str(bad))

    # ---------- [3] 单位后缀 ----------
    print("\n[3] 允许单位后缀")
    for line, num in (("📦 死信 0 条", "0"), ("🔁 今天 45 条评价", "45"),
                      ("📈 兜底率 5%", "5")):
        ok, bad = verify_line(line, allowed)
        check(f"{line} → 通过", ok, f"bad={bad}")

    # ---------- [4] 丢 1 条 → 保留其余，不重写 ----------
    print("\n[4] 丢 1 条 → 保留其余（不重写）")
    llm = FakeLLM(["📊 今天 123 个簇\n🎉 下降了 99%\n⚠️ 异常 7 次\n📦 死信 0 条"])
    r = generate(llm=llm, inputs=INPUTS)
    check("保留了 3 条", len(r.lines) == 3, f"{len(r.lines)} 条")
    check("丢了 1 条", len(r.dropped) == 1, f"{len(r.dropped)} 条")
    check("只调了 1 次（没重写）", r.attempts == 1 and llm.calls == 1, f"attempts={r.attempts}")
    check("没有退化成表格", r.fallback_table is False)

    # ---------- [5] 丢 >1 条 → 重写一次 ----------
    print("\n[5] 丢 >1 条 → 重写一次")
    llm = FakeLLM(["🎉 下降了 99%\n💸 涨了 88 元",        # 2 条都编造 → 重写
                   "📊 今天 123 个簇\n⚠️ 异常 7 次\n📦 死信 0 条"])
    r = generate(llm=llm, inputs=INPUTS)
    check("调了 2 次（重写了一次）", llm.calls == 2, f"calls={llm.calls}")
    check("重写后保留 3 条", len(r.lines) == 3, f"{len(r.lines)} 条")

    # ---------- [6] 重写后还不够 → 退化成表格 ----------
    print("\n[6] 重写后不足 3 条 → fallback_table=True")
    llm = FakeLLM(["🎉 下降了 99%", "💸 涨了 88 元", "🔥 涨了 77%"])
    r = generate(llm=llm, inputs=INPUTS, max_attempts=1)
    check("只剩 0 条", len(r.lines) == 0, f"{len(r.lines)} 条")
    check("退化成表格", r.fallback_table is True)

    # ---------- [7] 落库 ----------
    print("\n[7] 落进 analysis_run（stats.module=brief）")
    r = generate(llm=FakeLLM(["📊 今天 123 个簇\n⚠️ 异常 7 次\n📦 死信 0 条"]),
                 inputs=INPUTS)
    rid = save(r)
    check("有 run_id", bool(rid), rid)
    rows = latest(1)
    check("latest 能查到", bool(rows), f"{len(rows)} 条")
    if rows:
        check("text 非空", bool(rows[0]["text"]), rows[0]["text"][:40])
        check("trace_id 以 brief- 开头", rows[0]["trace_id"].startswith("brief-"),
              rows[0]["trace_id"])

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
