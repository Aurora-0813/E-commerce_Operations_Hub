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
from datetime import datetime
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
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 实测数据快照",
        "",
        "> ⚠️ **这个文件由脚本生成，不要手改。**",
        f"> 生成时间：{now}　生成命令：`python scripts/snapshot.py`",
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
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 已生成 {OUT}")
    print(f"   {len(lines)} 行")


if __name__ == "__main__":
    main()
