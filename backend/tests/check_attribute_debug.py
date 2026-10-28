# -*- coding: utf-8 -*-
"""
诊断：为什么某些格子的证据全被丢弃？

打印 LLM 的原始输出，以及每条 quote 和原文的精确对比。

运行（在 backend/ 目录下）：
    python tests/check_attribute_debug.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.analysis.attribute import (   # noqa: E402
    _build_prompt, _locate_quote, _parse_object,
)
from hub.llm import get_text_llm                                  # noqa: E402
from hub.persistence.repo_topic import ReviewTopicRepo            # noqa: E402


def inspect_cell(topic: str, platform: str) -> None:
    repo = ReviewTopicRepo()
    reviews = repo.fetch_cell_reviews(topic, platform, limit=10)
    if not reviews:
        print(f"\n### {topic}/{platform}：没有评价")
        return

    pairs = [(f"R{i + 1}", r.content_clean[:300]) for i, r in enumerate(reviews)]
    id_map = {rid: str(r.id) for (rid, _), r in zip(pairs, reviews)}
    content_by_id = {str(r.id): r.content_clean for r in reviews}

    print(f"\n{'=' * 70}\n### {topic}/{platform}（{len(reviews)} 条）\n{'=' * 70}")
    print("原文：")
    for rid, text in pairs:
        print(f"  [{rid}] {text}")

    raw = get_text_llm().chat(_build_prompt(topic, platform, pairs), max_tokens=1000)
    print(f"\n模型原始输出：\n{raw}")

    data = _parse_object(raw)
    if data is None:
        print("\n❌ 解析不出 JSON")
        return

    print(f"\nexplanation: {data.get('explanation')}")
    print("\n逐条核对 evidence（新匹配逻辑：三级匹配 + 反查编号）：")
    for e in data.get("evidence", []):
        if not isinstance(e, dict):
            continue
        rid = str(e.get("id", ""))
        quote = str(e.get("quote", ""))
        located = _locate_quote(quote, content_by_id, id_map.get(rid))
        if located is None:
            print(f"  ❌ [{rid}] {quote[:32]!r} → 定位失败（真的改写了原文）")
            continue
        real_id, exact = located
        moved = "" if id_map.get(rid) == real_id else "   ← 编号被纠正！"
        print(f"  ✅ [{rid}] {quote[:32]!r}{moved}")
        print(f"       → 实际来源 {real_id[:8]}…，原文片段 {exact[:40]!r}")


def main() -> None:
    # 失败的三个格子
    inspect_cell("quality", "taobao")
    inspect_cell("misship", "taobao")
    inspect_cell("misship", "jd")


if __name__ == "__main__":
    main()
