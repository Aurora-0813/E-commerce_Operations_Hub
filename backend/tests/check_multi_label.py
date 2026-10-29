# -*- coding: utf-8 -*-
"""
验证 3B 的多标签能力：**换三种输出格式**对比。

背景：
    真实买家常一句话说多个问题，单标签会丢信息。
    但第一版提示词（JSON 数组 + "列出所有问题"）让 3B 陷入复读循环。
    可能是"开放式列表"太难，换成更封闭的格式试试。

三种格式：
    P1  JSON 数组，topics 是数组（已知：会复读）
    P2  行格式 "R1: quality,size"，明确"最多 2 个"
    P3  JSON 数组，但每个对象只有一个 "problem" 字符串（逗号分隔）

运行（在 backend/ 目录下）：
    python tests/check_multi_label.py
"""
import json
import re
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.llm import get_text_llm   # noqa: E402

CASES = [
    ("衣服掉色严重，尺码还偏小", {"quality", "size"}),
    ("物流太慢，包装也压破了", {"logistics"}),
    ("开线了，纽扣也掉了两颗", {"quality"}),
    ("客服态度差，退款也不给退", {"service"}),
    ("发错尺码了，而且面料扎人", {"misship", "quality"}),
    ("色差太大，客服还不理人", {"desc_mismatch", "service"}),
    ("尺码偏小，面料起球", {"size", "quality"}),
    ("物流慢，客服态度也差", {"logistics", "service"}),
    ("衣服开线，掉色严重，还缩水", {"quality"}),
    ("发错货了，物流还慢", {"misship", "logistics"}),
]

TOPIC_DEFS = (
    "- quality        质量：开线/起球/掉色/缩水/线头/变形/面料差\n"
    "- size           尺码：偏大/偏小/版型不符\n"
    "- logistics      物流：慢/丢件/包装破损\n"
    "- service        服务：客服态度/响应慢/退款纠纷\n"
    "- desc_mismatch  描述不符：颜色/面料/厚薄和页面不一致\n"
    "- misship        错发漏发"
)


def build_p1(listing: str) -> str:
    return (f"你是电商评价分类助手。下面有 10 条差评，每条以 [R1] 到 [R10] 开头。\n\n"
            f"请判断每条**提到了哪些问题**（可能不止一个，最多 3 个）：\n{TOPIC_DEFS}\n\n"
            '输出 JSON 数组：[{"id": "R1", "topics": ["quality", "size"]}]\n\n'
            f"评价列表：\n{listing}\n")


def build_p2(listing: str) -> str:
    return (f"你是电商评价分类助手。下面有 10 条差评。\n\n"
            f"请判断每条提到了哪些问题（**最多 2 个**，只从下面选）：\n{TOPIC_DEFS}\n\n"
            "按这个格式输出，每条一行，不要其他内容：\n"
            "R1: quality,size\n"
            "R2: logistics\n\n"
            f"评价列表：\n{listing}\n")


def build_p3(listing: str) -> str:
    return (f"你是电商评价分类助手。下面有 10 条差评。\n\n"
            f"每条评价最多涉及 2 个问题，从下面选：\n{TOPIC_DEFS}\n\n"
            '输出 JSON 数组，problem 用逗号分隔（只有一个就不加逗号）：\n'
            '[{"id": "R1", "problem": "quality,size"}]\n\n'
            f"评价列表：\n{listing}\n")


def parse_p1(raw: str) -> dict[str, set]:
    """JSON 数组，topics 是列表。"""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data:
        if isinstance(item, dict):
            topics = item.get("topics") or []
            if not isinstance(topics, list):
                topics = [topics]
            out[str(item.get("id", ""))] = {str(t) for t in topics}
    return out


def parse_p2(raw: str) -> dict[str, set]:
    """行格式：R1: quality,size"""
    out = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(R\d+)\s*[:：]\s*(.+)", line)
        if not m:
            continue
        topics = {t.strip() for t in re.split(r"[,，/、\s]+", m.group(2)) if t.strip()}
        out[m.group(1)] = topics
    return out


def parse_p3(raw: str) -> dict[str, set]:
    """JSON 数组，problem 是逗号串。"""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data:
        if isinstance(item, dict):
            s = str(item.get("problem", ""))
            out[str(item.get("id", ""))] = {t.strip() for t in re.split(r"[,，/、\s]+", s) if t.strip()}
    return out


def main() -> None:
    n = len(CASES)
    listing = "\n".join(f"[R{i + 1}] {text}" for i, (text, _) in enumerate(CASES))
    llm = get_text_llm()
    print(f"模型：{llm.name}｜{n} 条多问题评价｜对比 3 种格式\n")

    variants = [
        ("P1 JSON 数组（topics 是列表）", build_p1, parse_p1),
        ("P2 行格式（最多 2 个）", build_p2, parse_p2),
        ("P3 JSON（problem 逗号串）", build_p3, parse_p3),
    ]

    results = []
    for label, builder, parser in variants:
        t0 = time.time()
        raw = llm.chat(builder(listing), max_tokens=800)
        elapsed = time.time() - t0
        got_map = parser(raw)

        exact = partial = wrong = 0
        details = []
        for i, (text, expected) in enumerate(CASES):
            rid = f"R{i + 1}"
            got = got_map.get(rid, set())
            if got == expected:
                exact += 1
                mark = "✅"
            elif got & expected:
                partial += 1
                mark = "⚠️"
            else:
                wrong += 1
                mark = "❌"
            details.append(f"    {mark} {text[:22]:<24}期望 {sorted(expected)} → 得到 {sorted(got)}")

        results.append((label, exact, partial, wrong, elapsed, raw, details))
        print(f"=== {label}（{elapsed:.1f}s）===")
        print(f"  完全正确 {exact}/{n}｜部分 {partial}｜全错 {wrong}")
        print("\n".join(details))
        print()

    print("=" * 70)
    print("汇总：")
    for label, exact, partial, wrong, elapsed, _, _ in results:
        print(f"  {label:<32} 完全正确 {exact}/{n}（{exact / n:.0%}）｜{elapsed:.1f}s")

    best = max(results, key=lambda r: r[1])
    print("\n=== 结论 ===")
    if best[1] / n >= 0.8:
        print(f"  ✅ 「{best[0]}」可用（{best[1]}/{n}），多标签可行")
    elif best[1] / n >= 0.4:
        print(f"  ⚠️ 「{best[0]}」部分可用（{best[1]}/{n}），建议折中方案")
    else:
        print(f"  ❌ 三种格式都不行（最好的 {best[1]}/{n}），3B 做不了多标签")


if __name__ == "__main__":
    main()
