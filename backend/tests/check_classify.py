# -*- coding: utf-8 -*-
"""
验证主题分类器：关键词兜底 / 单条分类 / 批量分类 / 故障降级。

运行（在 backend/ 目录下）：
    python tests/check_classify.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.analysis.classify import (   # noqa: E402
    IMPL_FALLBACK, classify_many, classify_one,
)
from hub.analysis.keywords import infer_topic   # noqa: E402
from hub.llm import get_text_llm                # noqa: E402

# 10 条覆盖 7 个主题的差评
SAMPLES = [
    "森马短袖T恤，开线了，穿几次就破了，太失望",          # quality
    "卫衣偏小，建议买大一码，XL穿起来像L",                # size
    "物流太慢了，下单十天还没到",                        # logistics
    "客服态度很差，问了半天不回复",                      # service
    "和图片颜色完全不一样，色差太大",                    # desc_mismatch
    "发错货了，我要的是黑色，给我发了个白色",            # misship
    "面料扎人，穿着不舒服",                              # quality
    "包装都压破了，里面的衣服也脏了",                    # logistics
    "质量一般般吧，也就这样",                            # other / quality
    "还行吧，就是有点小贵",                              # other
]


class BrokenLLM:
    """故意不听话的 LLM：用于测试三级降级。"""
    name = "broken-for-test"

    def chat(self, *args, **kwargs) -> str:
        return "我觉得这些评价都挺差的，但我说不清是什么问题。"


def main() -> None:
    # ---------- 第 1 步：关键词兜底（离线，不调模型）----------
    print("=== 第 1 步：关键词兜底（离线）===")
    kw_cases = [
        ("衣服开线了，穿一次就破", "quality"),
        ("尺码偏小，建议买大一码", "size"),
        ("物流太慢，等了一周", "logistics"),
        ("客服不理人", "service"),
        ("和图片不一样，色差大", "desc_mismatch"),
        ("发错货了", "misship"),
        ("无话可说", None),
    ]
    for text, expect in kw_cases:
        got = infer_topic(text)
        flag = "✅" if got == expect else "⚠️"
        print(f"  {flag} {text:<22} → {got}（期望 {expect}）")

    llm = get_text_llm()
    print(f"\n模型：{llm.name}\n")

    # ---------- 第 2 步：单条分类（真 LLM）----------
    print("=== 第 2 步：单条分类（真 LLM）===")
    for text in SAMPLES[:3]:
        t0 = time.time()
        r = classify_one(llm, "test", text)
        print(f"  [{time.time() - t0:.1f}s] {r['topic']:<14} s={r['severity']} "
              f"conf={r['confidence']} | {text[:24]}")

    # ---------- 第 3 步：批量 10 条 ----------
    print("\n=== 第 3 步：批量 10 条（真 LLM）===")
    items = [{"record_id": f"r{i}", "text": t} for i, t in enumerate(SAMPLES)]
    t0 = time.time()
    results, stats = classify_many(llm, items, batch_size=10)
    elapsed = time.time() - t0

    print(f"\n  耗时 {elapsed:.1f}s")
    print(f"  总数 {stats.total}｜批量成功 {stats.batch_ok}｜"
          f"逐条救回 {stats.retry_ok}｜兜底 {stats.fallback}｜批次数 {stats.batches}")
    print("\n  分类结果：")
    for r in results:
        print(f"    {r['topic']:<14} s={r['severity']} conf={r['confidence']:<5} "
              f"{r['impl']:<13} degraded={r['degraded']}")

    # ---------- 第 4 步：故障注入（测试三级降级）----------
    print("\n=== 第 4 步：故障注入（LLM 只会说废话）===")
    bad_results, bad_stats = classify_many(
        BrokenLLM(), items[:3], batch_size=3, model_version="test"
    )
    print(f"  批量成功 {bad_stats.batch_ok}｜逐条救回 {bad_stats.retry_ok}｜"
          f"兜底 {bad_stats.fallback}")
    for r in bad_results:
        print(f"    {r['topic']:<14} {r['impl']} degraded={r['degraded']}")
    ok = all(r["impl"] == IMPL_FALLBACK and r["degraded"] for r in bad_results)
    print(f"  {'✅ 全部正确降级到关键词兜底' if ok else '❌ 降级逻辑有问题'}")


if __name__ == "__main__":
    main()
