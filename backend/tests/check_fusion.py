# -*- coding: utf-8 -*-
"""
验证决策融合：规则优先、ML 阈值、差评保护。

运行（在 backend/ 目录下）：
    python tests/check_fusion.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.dqe.fusion import decide          # noqa: E402
from hub.dqe.rules import RuleHit          # noqa: E402

R006 = RuleHit("R006", "灌水模板")
R008 = RuleHit("R008", "Prompt 注入", downgradable=False)

# (rating, hits, spam_score, 期望 decision, 期望 reason, 说明)
CASES = [
    (5, [R006], 0.00, "drop", "R006", "好评 + 模板 → drop"),
    (1, [R006], 0.00, "review", "R006", "差评 + 模板 → 降级 review（差评保护）"),
    (1, [R008], 0.00, "drop", "R008", "差评 + 注入 → 不降级，硬 drop"),
    (1, [R006, R008], 0.00, "drop", "R006",
     "差评 + 模板&注入 → 有不可降级项，仍 drop"),
    (5, [], 0.90, "drop", "ML_SPAM", "好评 + ML 0.90 ≥ 0.75 → drop"),
    (1, [], 0.95, "review", "ML_SPAM", "差评 + ML 0.95 ≥ 0.90 → 降级 review"),
    (1, [], 0.85, "keep", "OK", "差评 + ML 0.85 < 差评阈值 0.90 → keep"),
    (5, [], 0.50, "keep", "OK", "好评 + ML 0.50 < 0.75 → keep"),
    (3, [], 0.00, "keep", "OK", "中评无命中 → keep（中评不受保护）"),
]


def main() -> None:
    print("=== 决策融合用例 ===")
    all_ok = True
    for rating, hits, spam, expect_dec, expect_reason, desc in CASES:
        v = decide(
            review_id="r1",
            rating=rating,
            rule_hits=hits,
            spam_score=spam,
        )
        ok = (v.decision == expect_dec and v.reason_code == expect_reason)
        all_ok = all_ok and ok
        print(f"{'✅' if ok else '❌'} {desc}")
        print(f"   rating={rating} hits={[h.rule_id for h in hits]} spam={spam}")
        print(f"   → decision={v.decision} reason={v.reason_code}"
              f"  期望 {expect_dec}/{expect_reason}")

    # 另外验证降级时 engine 标签
    print("\n=== 降级标签 ===")
    v = decide(review_id="r1", rating=5, rule_hits=[], spam_score=0.0,
               engine="rule_only")
    print(f"engine=rule_only → {v.engine}（应为 rule_only）")

    print("\n" + ("✅ 全部通过" if all_ok else "❌ 有失败"))


if __name__ == "__main__":
    main()
