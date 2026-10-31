# -*- coding: utf-8 -*-
"""
生成"数据快照"（docs/实测数据.md）
==================================
**这是全项目唯一允许写精确数字的地方。**

为什么要有它（`docs/工程规范.md` §6「文档不写死数字」）：
    M9 之前改了一次 mock 数据，`3981 / 2318 / 450 / 14 / 4` 这些写死在
    README 和开发思路里的数字**全部作废**，而且散在 5 个文件的 26 处，
    没人记得该改哪几处——我自己就在工程规范里预言过这个坑，然后踩了进去。

    修法不是"这次认真改一遍"，而是**让数字只有一个来源**：
        这个脚本读数据库 + 最近一次 trace，生成 `docs/实测数据.md`。
        文档正文只写结论和量级，精确值一律指向这个文件。

用法（在 backend/ 目录下）：
    python scripts/snapshot.py          # 重新生成
    # 或者跑完全链后：python scripts/run_all.py 的末尾会自动调用（见该文件）
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.observability.health import collect as collect_health   # noqa: E402
from hub.observability.status import collect as collect_status   # noqa: E402

OUT = _BACKEND_DIR.parent / "docs" / "实测数据.md"


def _render(sections) -> list[str]:
    lines: list[str] = []
    for sec_name, pairs in sections:
        lines += [f"#### {sec_name}", "", "| 指标 | 值 |", "| --- | --- |"]
        for k, v in pairs:
            lines.append(f"| {k} | {v} |")
        lines.append("")
    return lines


def main() -> None:
    lines = [
        "# 实测数据快照",
        "",
        "> ⚠️ **这个文件由脚本生成，不要手改。**",
        # ------------------------------------------------------------------
        # ⚠️ **这里刻意不写"生成时间"** ✅
        #
        # 踩过（2026-10-31）：原来写了 `生成时间：2026-10-31 09:13`，
        # 结果 `--check` 每次都说"发现 2 行差异"，而那 2 行只有时间戳 ✅
        #   ↓
        # 一个每次都报差异的对照工具 = 没人看 = 等于没有 ✅ ✗
        #   ↓
        # **非确定性内容会让对照工具失效** ✅
        # "什么时候生成的"这个信息，`git log` 天然就有，不需要写进文件 ✅
        # ------------------------------------------------------------------
        "> 生成命令：`python scripts/snapshot.py`"
        "　｜　想看差异：`python scripts/snapshot.py --check`",
        ">",
        "> 其他文档只写结论和量级，精确数字一律看这里"
        "（原因见 `docs/工程规范.md` §6「文档不写死数字」）。",
        "",
        "## 当前数据（库里现在什么样）",
        "",
    ]
    lines += _render(collect_status())
    lines += ["## 最近一次运行（跑一次花了多少）", ""]
    lines += _render(collect_health())
    lines += [
        "## 效果复盘（M9）",
        "",
        "预埋剧本的 ground truth 在 `data/mock/historical_interventions.csv`，",
        "复盘结果用 `python scripts/show_outcomes.py` 看。",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    new = "\n".join(lines)

    # ------------------------------------------------------------------
    # --check：不动文件，只比"现在"和"上次生成的"差在哪
    #
    # 为什么需要它：改完代码后要回答"**我这次改动有没有影响任何数字**" ✅
    #   手工做法是"备份 → 重新生成 → Compare-Object → 删备份" ✅
    #   一条命令就能做完，而且**不污染文件** ✅
    #
    # 用法：python scripts\snapshot.py --check
    #   无差异 → 打印"数字一致"，退出码 0
    #   有差异 → 逐行打印，退出码 1（可以放进 run_tests.py / CI）
    # ------------------------------------------------------------------
    if "--check" in sys.argv:
        if not old:
            print("⚠️ 还没有生成过快照（先跑一次 python scripts\\snapshot.py）")
            sys.exit(2)
        if old.strip() == new.strip():
            print("✅ 数字与上次快照完全一致（改动没有影响任何结果）")
            sys.exit(0)

        import difflib

        diff = [ln for ln in difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="上次快照", tofile="当前数据", lineterm="", n=1,
        ) if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
        print(f"⚠️ 发现 {len(diff)} 行差异（上次快照 vs 当前数据库）：\n")
        for ln in diff:
            print(f"  {ln}")
        print("\n如果这些差异是**故意的**（比如重跑了全链），")
        print("跑一次 `python scripts\\snapshot.py` 更新快照即可 ✅")
        print("如果不是故意的 → 说明某处改动影响了结果，要查。")
        sys.exit(1)

    OUT.write_text(new, encoding="utf-8")
    print(f"✅ 已生成 {OUT}")
    print(f"   {len(lines)} 行")
    if old and old.strip() != new.strip():
        print("   （和上一版有差异 —— 想先看差异用 --check，不要直接覆盖）")


if __name__ == "__main__":
    main()
