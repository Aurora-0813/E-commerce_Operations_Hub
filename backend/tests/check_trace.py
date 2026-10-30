# -*- coding: utf-8 -*-
"""
检查点 1：Trace 树

验 6 件事：
    ① 嵌套自动建立父子关系（不用手传 parent_id）
    ② 时间戳带时区（UTC aware，和 DateTime(timezone=True) 匹配）
    ③ token 能挂到 span 上
    ④ 相同名字的兄弟会折叠成 ×N
    ⑤ 异常时 span 标记 ERROR 且照常落库
    ⑥ 树能按深度优先打印出来

运行（在 backend/ 目录下）：
    python tests/check_trace.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.core.tracing import (                     # noqa: E402
    print_tree,
    set_trace_id,
    span,
    trace_tree,
)

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def main() -> None:
    tid = set_trace_id()
    print(f"trace_id = {tid}\n")

    with span("outer", kind="step", foo=1) as sp:
        sp.add(note="顶层")
        with span("inner.a"):
            time.sleep(0.05)
        with span("inner.b"):
            time.sleep(0.02)
            with span("innermost", kind="llm") as sp2:
                time.sleep(0.01)
                sp2.add_tokens(100, 20)       # 假装调了一次 LLM
        # 三个同名兄弟 → 应该折叠成 ×3
        for i in range(3):
            with span("repeat", i=i):
                time.sleep(0.01)
        # 抛异常的 span
        try:
            with span("boom"):
                raise ValueError("故意的")
        except ValueError:
            pass

    rows = trace_tree(tid)
    by_name = {r["name"]: r for r in rows}

    # ---------- ① 父子关系 ----------
    print("=== ① 嵌套 ===")
    check("顶层只有 1 个（outer）",
          [r["name"] for r in rows if r["depth"] == 0] == ["outer"])
    check("inner.a 的父是 outer",
          by_name["inner.a"]["parent_id"] == by_name["outer"]["span_id"])
    check("innermost 的父是 inner.b（三层嵌套）",
          by_name["innermost"]["parent_id"] == by_name["inner.b"]["span_id"])
    check("depth 正确", by_name["outer"]["depth"] == 0
          and by_name["inner.b"]["depth"] == 1
          and by_name["innermost"]["depth"] == 2)

    # ---------- ② 时区 ----------
    print("\n=== ② 时区 ===")
    st = by_name["outer"]["start_ts"]
    check("时间戳带时区（UTC aware）", st.tzinfo is not None, str(st))

    # ---------- ③ token ----------
    print("\n=== ③ token ===")
    check("token 挂上了", by_name["innermost"]["tokens_in"] == 100
          and by_name["innermost"]["tokens_out"] == 20,
          f"{by_name['innermost']['tokens_in']}/{by_name['innermost']['tokens_out']}")

    # ---------- ④ 折叠 ----------
    print("\n=== ④ 折叠同名兄弟 ===")
    repeats = [r for r in rows if r["name"] == "repeat"]
    check("3 个 repeat 折叠成 1 条 ×3",
          len(repeats) == 1 and repeats[0]["_count"] == 3,
          f"{len(repeats)} 条，count={repeats[0]['_count'] if repeats else 0}")

    # ---------- ⑤ 异常 ----------
    print("\n=== ⑤ 异常 ===")
    check("异常的 span 标 ERROR 且没丢",
          by_name.get("boom", {}).get("status") == "ERROR")
    check("错误信息记在 attrs 里",
          "故意的" in str(by_name.get("boom", {}).get("attrs", {})))

    # ---------- ⑥ 打印 ----------
    print("\n=== ⑥ 打印树 ===")
    print()
    print_tree(tid)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ 检查点 1 通过：嵌套 / 时区 / token / 折叠 / 异常 / 打印 都正常")


if __name__ == "__main__":
    main()
