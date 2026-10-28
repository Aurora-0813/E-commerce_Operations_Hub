# -*- coding: utf-8 -*-
"""
验证归因器：真 LLM 归因 / 预过滤编造证据 / 契约双保险。

运行（在 backend/ 目录下）：
    python tests/check_attribute.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.analysis.attribute import attribute_cell   # noqa: E402
from hub.llm import get_text_llm                    # noqa: E402

# 10 条 quality 类差评（模拟"格子"里的代表评价）
QUALITY_REVIEWS = [
    "森马短袖T恤，开线了，穿几次就破了，太失望",
    "起球严重，面料太差，穿两次就起球了",
    "掉色，洗一次就变旧了，水都变色了",
    "缩水了，洗后小了一圈，没法穿了",
    "线头很多，做工粗糙，看着很廉价",
    "面料很透，质量差，不透气",
    "洗了一次就变形了，根本没法穿",
    "面料扎人，穿着不舒服",
    "纽扣掉了两颗，质量堪忧",
    "袖口开线，刚穿一天就散了",
]


class LiarLLM:
    """完全编造证据：quote 在原文里根本不存在。"""
    name = "liar"

    def chat(self, *args, **kwargs) -> str:
        return (
            '{"explanation": "质量问题严重", '
            '"evidence": [{"id": "R1", "quote": "这句话原文里根本没有"}]}'
        )


class HalfLiarLLM:
    """一半真一半假：R1 的 quote 是真的，R2 是编的。"""
    name = "half-liar"

    def chat(self, *args, **kwargs) -> str:
        return (
            '{"explanation": "开线和起球是主要问题", '
            '"evidence": ['
            '{"id": "R1", "quote": "开线了，穿几次就破了"},'
            '{"id": "R2", "quote": "这句话是编造的"}'
            ']}'
        )


def main() -> None:
    reviews = [{"review_id": f"id{i}", "text": t} for i, t in enumerate(QUALITY_REVIEWS)]
    content_by_id = {r["review_id"]: r["text"] for r in reviews}

    # ---------- 第 1 步：真 LLM 归因 ----------
    llm = get_text_llm()
    print(f"=== 第 1 步：真 LLM 归因（model={llm.name}）===")
    t0 = time.time()
    result = attribute_cell(llm, "quality", "taobao", reviews, model_version=llm.name)
    elapsed = time.time() - t0

    if result is None:
        print("❌ 归因失败（返回 None）")
    else:
        print(f"耗时 {elapsed:.1f}s")
        print(f"  root_cause   = {result['topic']}")
        print(f"  confidence   = {result['confidence']}（规则算）")
        print(f"  degraded     = {result['degraded']}")
        print(f"  dropped      = {result['dropped']}（丢弃的编造证据数）")
        print(f"  explanation  = {result['explanation']}")
        print(f"  evidence（{len(result['evidence'])} 条）：")
        all_ok = True
        for ev in result["evidence"]:
            grounded = ev["quote"] in content_by_id[ev["review_id"]]
            all_ok = all_ok and grounded
            print(f"    {'✅' if grounded else '❌'} [{ev['review_id']}] {ev['quote']}")
        print(f"\n  {'✅ 所有证据都 grounded（在原文里能找到）' if all_ok else '❌ 有证据不 grounded'}")

    # ---------- 第 2 步：完全编造 ----------
    print("\n=== 第 2 步：完全编造证据的模型 ===")
    r2 = attribute_cell(LiarLLM(), "quality", "taobao", reviews)
    print(f"  返回：{r2}")
    print(f"  {'✅ 正确拒绝（全是编造，返回 None）' if r2 is None else '❌ 编造证据竟然通过了'}")

    # ---------- 第 3 步：一半真一半假 ----------
    print("\n=== 第 3 步：一半真一半假的模型 ===")
    r3 = attribute_cell(HalfLiarLLM(), "quality", "taobao", reviews)
    if r3 is None:
        print("  ❌ 返回 None（应该保留真证据）")
    else:
        print(f"  证据 {len(r3['evidence'])} 条｜丢弃 {r3['dropped']} 条｜"
              f"degraded={r3['degraded']}｜confidence={r3['confidence']}")
        for ev in r3["evidence"]:
            ok = ev["quote"] in content_by_id[ev["review_id"]]
            print(f"    {'✅' if ok else '❌'} {ev['quote']}")
        passed = (len(r3["evidence"]) == 1 and r3["dropped"] == 1
                  and r3["degraded"] is True)
        print(f"  {'✅ 保留了真证据、丢弃了假证据、标记 degraded' if passed else '❌ 预过滤逻辑有问题'}")


if __name__ == "__main__":
    main()
