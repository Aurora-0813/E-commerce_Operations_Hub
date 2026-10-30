# -*- coding: utf-8 -*-
"""
检查：经验回灌的【决策规则】（check_experience_rules.py）
=========================================================
用**构造出来的历史**穷举两条规则的边界，不碰数据库。

为什么不用真数据测：
    我们现在只有 4 个历史动作，每个 (topic, 工具) 组合最多 1 次，
    永远在门槛之下 → 真实数据测不到规则本身。
    但规则是这一层唯一的逻辑，必须被验证。

    所以：`decide()` 是纯函数（收 list[ToolRecord] → 返回 Advice），
    这里直接把战绩构造出来，逐条断言。

覆盖的边界：
    规则 1（收紧，门槛 2）：
        0 次负向 → 不动
        1 次负向 → **不动**（门槛生效，这是最关键的一条）
        2 次负向 → 收紧
        2 次负向但 3 次正向（负向不占多数）→ 不收紧
    规则 2（放开，门槛 3）：
        2 次正向 → 不动
        3 次正向且无其他 → 优先
        3 次正向但有 1 次不明确 → **不动**（不明确会破坏证据）
        3 次正向但有 1 次负向 → 不动
    空历史：
        没有记录 → 不改行为，提示用默认规则

运行（在 backend/ 目录下）：
    python tests/check_experience_rules.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.action.experience import ToolRecord, decide   # noqa: E402

PASS, FAIL = [], []
DEFAULT = "review.draft_reply"


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))


def rec(tool: str, pos: int = 0, neg: int = 0, inc: int = 0, pend: int = 0):
    return ToolRecord(tool=tool, positive=pos, negative=neg,
                      inconclusive=inc, pending=pend)


def main() -> None:
    print("=" * 62)
    print("  经验回灌的决策规则（构造数据）")
    print("=" * 62)

    print("\n[1] 空历史 → 不改行为")
    a = decide([], "quality", DEFAULT)
    check("不给建议", not a.has_advice)
    check("理由里提到默认规则", DEFAULT in a.reason, a.reason[:40])

    print("\n[2] 规则 1：负向 ≥2 才收紧")
    a = decide([rec("listing.update_desc", neg=0, pos=5)], "quality", DEFAULT)
    check("0 次负向 → 不收紧", not a.escalate)

    a = decide([rec("listing.update_desc", neg=1, pos=0)], "quality", DEFAULT)
    check("1 次负向 → 不改行为（门槛生效）", not a.escalate, a.reason[:44])
    check("1 次负向 → 理由写'证据不足'", "证据不足" in a.reason)

    a = decide([rec("listing.update_desc", neg=2, pos=0)], "quality", DEFAULT)
    check("2 次负向 → 收紧", a.escalate)
    check("指向出问题的那个工具", a.escalate_tool == "listing.update_desc")
    check("理由里有次数", "2" in a.reason, a.reason[:44])

    a = decide([rec("listing.update_desc", neg=2, pos=3)], "quality", DEFAULT)
    check("2 负 + 3 正（负向不占多数）→ 不收紧", not a.escalate)

    print("\n[3] 规则 2：正向 ≥3 才放开")
    a = decide([rec("listing.update_desc", pos=2)], "quality", DEFAULT)
    check("2 次正向 → 不优先", a.prefer_tool is None, a.reason[:44])

    a = decide([rec("listing.update_desc", pos=3)], "quality", DEFAULT)
    check("3 次正向 → 优先", a.prefer_tool == "listing.update_desc")

    a = decide([rec("listing.update_desc", pos=3, inc=1)], "quality", DEFAULT)
    check("3 正 + 1 次不明确 → 不优先（证据被污染）", a.prefer_tool is None)

    a = decide([rec("listing.update_desc", pos=3, neg=1)], "quality", DEFAULT)
    check("3 正 + 1 负 → 不优先", a.prefer_tool is None)

    print("\n[4] 两条规则的优先级：收紧 > 放开")
    a = decide([rec("A", pos=5), rec("B", neg=2)], "quality", DEFAULT)
    check("同时有证据时，先收紧", a.escalate and a.prefer_tool is None,
          "收紧要快")
    check("收紧指向的是 B（有负向的那个）", a.escalate_tool == "B")

    print("\n[5] 不对称性自证")
    check("1 次负向不改行为，但 2 次就改（比正向快）", True)
    check("3 次正向才改（比负向慢）", True)

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)}｜失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"    ❌ {f}")
    print("=" * 62)
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
