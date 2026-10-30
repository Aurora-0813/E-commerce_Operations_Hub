# -*- coding: utf-8 -*-
"""
检查：经验建议怎么应用到提案（check_advice_wiring.py）
=======================================================
测 `proposer._apply_advice()` —— 它是"经验"和"提案"之间的接线。

**为什么必须测这一步**：
    在真实数据上它永远返回 (默认工具, None)，因为样本不到门槛。
    所以"接线有没有接错"在真实数据上**看不出来**。
    而接错的后果很具体：库里写着 risk_level=L2、实际却自动执行了
    —— **看起来降级了，其实没降**，是最危险的一种"假安全"。

运行（在 backend/ 目录下）：
    python tests/check_advice_wiring.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.action.experience import Advice          # noqa: E402
from hub.action.proposer import _apply_advice     # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))


DEFAULT = "review.draft_reply"


def main() -> None:
    print("=" * 62)
    print("  经验建议 → 提案 的接线")
    print("=" * 62)

    print("\n[1] 没有建议（真实数据的常态）")
    t, floor, note = _apply_advice("quality", DEFAULT, Advice(topic="quality"))
    check("工具用默认的", t == DEFAULT, t)
    check("不降级", floor is None)
    check("不写经验说明", note == "")

    print("\n[2] adv = None（防御性：调用方没传）")
    t, floor, note = _apply_advice("quality", DEFAULT, None)
    check("工具用默认的", t == DEFAULT)
    check("不降级", floor is None)

    print("\n[3] 正向证据充足 → 换工具")
    a = Advice(topic="quality", prefer_tool="listing.update_desc",
               reason="3 次全部有效")
    t, floor, note = _apply_advice("quality", DEFAULT, a)
    check("工具被替换", t == "listing.update_desc", t)
    check("不降级（换工具不等于降级）", floor is None)
    check("说明带上理由", "3 次" in note, note)

    print("\n[4] 负向证据充足 → 降级")
    a = Advice(topic="quality", escalate=True, escalate_tool=DEFAULT,
               reason="2 次导致差评率上升")
    t, floor, note = _apply_advice("quality", DEFAULT, a)
    check("工具不变（只加约束，不换工具）", t == DEFAULT)
    check("**风险下限升到 L2**", floor == "L2")
    check("说明带上理由", "2 次" in note, note)

    print("\n[5] 两者同时出现（规则上不会，但防御性检查）")
    a = Advice(topic="quality", prefer_tool="listing.update_desc",
               escalate=True, escalate_tool=DEFAULT, reason="混合")
    t, floor, note = _apply_advice("quality", DEFAULT, a)
    check("降级优先（保守）", floor == "L2")
    check("工具仍然按建议替换", t == "listing.update_desc")

    print("\n[6] 接线要点（这条是给人看的）")
    print("  ⚠️ 提案写进库的 risk_level 必须被 cli.cmd_propose 传给网关的")
    print("     CallContext.risk_floor，否则降级只写库、不生效。")
    print("     网关侧还有一层 max(工具声明, 下限)，保证只能升不能降")
    print("     （见 tests/check_risk_floor.py）。")

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)}｜失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"    ❌ {f}")
    print("=" * 62)
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
