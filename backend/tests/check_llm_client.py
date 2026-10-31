# -*- coding: utf-8 -*-
"""
LLM 客户端验证（check_llm_client.py）—— 阶段 1.1

验什么：
    1. OpenAI 兼容协议能连通（本地 Ollama 的 /v1）
    2. base_url 配错时【构造期】就报错，而不是跑到 M3 才 404
    3. **确定性**：temperature=0 + 固定 seed，连跑 5 次看几次一致

为什么要单独测确定性：
    项目卖点之一是"可复现"。如果换个协议就把可复现弄丢了，
    所有"改前 / 改后"的对比就都不成立。所以必须量化它，不能靠"感觉"。
    实测（2026-10-31）：Ollama 的 /v1 兼容层即使 temperature=0 也不保证逐字一致，
    所以本测试的产出是【一个比例】，不是一个"通过/失败"。

跑法：python tests/check_llm_client.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.llm import get_text_llm
from hub.llm.client import OpenAIText


def main() -> int:
    print("=" * 62)
    print("  LLM 客户端（OpenAI 兼容协议）")
    print("=" * 62)

    llm = get_text_llm()
    print(f"\nbase_url = {llm.base_url}")
    print(f"model    = {llm.model}")

    # ---------- 1. 连通性 ----------
    print("\n[1] 连通性")
    out = llm.chat("用一句话说明什么是差评。", temperature=0.0, max_tokens=60)
    print(f"  [OK] 拿到 {len(out)} 字：{out[:60]}")

    # ---------- 2. 配置错误在构造期就报 ----------
    print("\n[2] 配错 base_url 必须在构造期报错")
    try:
        OpenAIText(base_url="http://localhost:11434", model="qwen2.5:7b")
        print("  [FAIL] 居然没报错 —— 会跑到 M3 才 404")
        return 1
    except ValueError as e:
        print(f"  [OK] {str(e)[:70]}")

    # ---------- 3. 确定性（5 次） ----------
    print("\n[3] 确定性：同 prompt 连跑 5 次（temperature=0 + seed=42）")
    prompt = ("把这句话归为『质量问题』或『物流问题』，只回类别名："
              "这件衣服洗一次就掉色了。")
    outs = [llm.chat(prompt, temperature=0.0, max_tokens=20) for _ in range(5)]
    cnt = Counter(outs)
    top, top_n = cnt.most_common(1)[0]
    print(f"  不同回答数：{len(cnt)}")
    for text, n in cnt.most_common():
        print(f"    {n} 次：{text[:40]!r}")
    ratio = top_n / len(outs)
    print(f"  最高频答案占比：{ratio:.0%}（{top_n}/{len(outs)}）")

    if len(cnt) == 1:
        print("  [OK] 5 次完全一致 → 可作门禁基线")
    elif ratio >= 0.8:
        print("  [!] 有抖动但多数一致 → 门禁只能断言【结构】，不能断言【逐字相同】")
    else:
        print("  [X] 抖动过大 → 分类/归因的对比实验要取多次众数")

    print("\n" + "=" * 62)
    print("  结论：协议通了；确定性按上面的比例写进文档，不要写『完全可复现』")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
