# -*- coding: utf-8 -*-
"""
量化评测噪声（measure_noise.py）
================================
**同样的输入，跑 N 次分类，看准确率散多大。**

为什么必须做这件事：
    temperature=0 也**不保证**完全确定（GPU 浮点、批处理顺序、并行推理）。
    72 条样本上，差 3 条就是 ±4.2%。
    → 如果不先知道噪声有多大，就**无法判断"改了提示词之后准确率变化"是不是真的**。

2026-10 实测背景（这就是写这个脚本的原因）：
    同一个提示词在不同轮次跑出 93.1% / 95.8% / 97.2%，
    而我们当时把这三次差异**当成"提示词改动生效了"在解释**——
    那是把噪声当信号。

用法（在 backend/ 目录下，约 4 分钟/轮）：
    python scripts/measure_noise.py --n 3
"""
import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 复用 eval_topic 的取数与比对逻辑，避免两套口径
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))
from eval_topic import load_gold, load_pred          # noqa: E402

from hub.analysis.engine import run_topic_analysis   # noqa: E402


def one_round(i: int) -> tuple[float, int, int]:
    """跑一轮：全量重分类 + 评估。返回 (准确率, 正确数, 总数)。"""
    print(f"\n{'=' * 56}\n第 {i} 轮：全量重分类…\n{'=' * 56}")
    st = run_topic_analysis(force=True)
    print(f"  多标签 {st.multi_label} 条｜兜底 {st.fallback} 条｜耗时 {st.elapsed_s}s")

    gold = load_gold()
    pred = load_pred(list(gold))
    common = sorted(set(gold) & set(pred))
    correct = sum(1 for k in common if gold[k] == pred[k])
    acc = correct / len(common) if common else 0.0
    print(f"  准确率 {correct}/{len(common)} = {acc:.1%}")
    return acc, correct, len(common)


def main() -> None:
    ap = argparse.ArgumentParser(description="量化 J3 分类的评测噪声")
    ap.add_argument("--n", type=int, default=3, help="跑几轮（默认 3）")
    args = ap.parse_args()

    results = []
    for i in range(1, args.n + 1):
        results.append(one_round(i))

    accs = [r[0] for r in results]
    print(f"\n{'=' * 56}")
    print(f"  {args.n} 轮结果：{'  '.join(f'{a:.1%}' for a in accs)}")
    spread = (max(accs) - min(accs)) * 100
    print(f"  极差 {spread:.1f} 个百分点（{max(accs) - min(accs):+.1%} 满分对应 "
          f"{round(spread / 100 * results[0][2])} 条）")
    print(f"{'=' * 56}")
    print("\n结论怎么用：")
    print("  · 以后任何提示词改动的准确率变化，**小于这个极差就不能算改进**")
    print("  · 要真正比较两个提示词，得把评测集从 72 扩到 300+")
    print("    （已在 docs/工程规范.md 的推迟清单里）")


if __name__ == "__main__":
    main()
