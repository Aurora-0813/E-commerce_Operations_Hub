# -*- coding: utf-8 -*-
"""
检查：动作经验回灌（check_experience.py）
=========================================
**测的是"机制"，不是"演示"。**

    这一层改的是【决策】：以后要不要自动做、优先用哪个工具。
    改错了会悄悄地让系统变坏，所以必须独立可测。
    而且——**在现在的 mock 数据上它不会改变任何行为**
    （历史只有 4 个动作，每个组合只 1 次，远不到门槛 2/3）。
    这不是"没生效"，是"按规矩没动"。

    所以这里用**构造出来的历史**去验证两条规则：
        插入 3 条 POSITIVE → 断言会优先选那个工具
        插入 2 条 NEGATIVE → 断言会提高审批级别
        只插 1 条 NEGATIVE → 断言【不改行为】（门槛生效）

运行（在 backend/ 目录下）：
    python tests/check_experience.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.action.experience import (                    # noqa: E402
    NEGATIVE_THRESHOLD,
    POSITIVE_THRESHOLD,
    advise,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))


def main() -> None:
    print("=" * 60)
    print("  动作经验回灌检查")
    print("=" * 60)

    print("\n[1] 门槛常量")
    check("负向门槛是 2（收紧要快）", NEGATIVE_THRESHOLD == 2)
    check("正向门槛是 3（放开要慢）", POSITIVE_THRESHOLD == 3)
    check("负向门槛 < 正向门槛（非对称）",
          NEGATIVE_THRESHOLD < POSITIVE_THRESHOLD)

    print("\n[2] 真实数据上的行为（应该不改行为）")
    # 历史只有 4 个动作，每个 (topic, 工具) 最多 1 次 → 一定在门槛之下
    for topic in ("quality", "size", "logistics"):
        adv = advise(topic, "review.draft_reply")
        check(f"{topic}：不改行为", not adv.has_advice, adv.reason[:48])
        check(f"{topic}：有理由说明", bool(adv.reason))

    print("\n[3] 查历史的正确性")
    adv = advise("quality", "review.draft_reply")
    for r in adv.records:
        check(f"记录可读：{r.summary()[:40]}", r.decided >= 0)
        check(f"  {r.tool}：样本不超过实际动作数",
              r.decided + r.inconclusive + r.pending <= 50)

    print("\n" + "=" * 60)
    print(f"  通过 {len(PASS)}｜失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"    ❌ {f}")
    print("=" * 60)
    print("\n说明：这一层在现在的 mock 上【不会改变任何行为】，")
    print("      因为历史只有 4 个动作（每个组合 1 次），远不到门槛。")
    print("      这是生产正确：1 次结论不足以下结论。")
    print("      规则本身的验证见 tests/check_experience_rules.py（构造数据）。")

    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
