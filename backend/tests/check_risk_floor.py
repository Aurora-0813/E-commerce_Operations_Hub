# -*- coding: utf-8 -*-
"""
检查：风险下限只能升不能降（check_risk_floor.py）
=================================================
`CallContext.risk_floor` 是"经验回灌"用来要求人工确认的通道。
**它必须只能升不能降** —— 允许降就等于"有一次历史成功就能让改价自动执行"，
那样整个项目讲的红线就没了。

运行（在 backend/ 目录下）：
    python tests/check_risk_floor.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.gateway.invoke import _effective_risk      # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))


def main() -> None:
    print("=" * 58)
    print("  风险下限：只能升，不能降")
    print("=" * 58)

    print("\n[1] 没有下限时，保持工具声明")
    check("L0 → L0", _effective_risk("L0", None) == "L0")
    check("L3 → L3", _effective_risk("L3", None) == "L3")

    print("\n[2] 下限更高时，取高的（升）")
    check("L1 + 下限 L2 → L2", _effective_risk("L1", "L2") == "L2")
    check("L1 + 下限 L3 → L3", _effective_risk("L1", "L3") == "L3")

    print("\n[3] 下限更低时，**不降**（这是关键）")
    check("L3 + 下限 L1 → 仍是 L3", _effective_risk("L3", "L1") == "L3")
    check("L2 + 下限 L0 → 仍是 L2", _effective_risk("L2", "L0") == "L2")
    check("L2 + 下限 L1 → 仍是 L2", _effective_risk("L2", "L1") == "L2")

    print("\n[4] 相同等级")
    check("L2 + 下限 L2 → L2", _effective_risk("L2", "L2") == "L2")

    print("\n" + "=" * 58)
    print(f"  通过 {len(PASS)}｜失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"    ❌ {f}")
    print("=" * 58)
    print("\n意义：允许降级就等于'统计证据可以拆掉人工审批红线'。")
    print("      经验只能让人更谨慎，不能让人更放手。")

    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
