# -*- coding: utf-8 -*-
"""
A/B 实验：TF-IDF+SVD vs bge-m3 嵌入，哪个聚类更好？

**前置**：先拉模型（约 1.2GB）
    ollama pull bge-m3

设计：
    只换"向量化方式"，其余完全一样（同样的 topic 分组、同样的 k、同样的 KMeans）。
    否则比较不公平。

    另外用一组**人工判定**的模板对做定向检查：
        SHOULD_MERGE     不同措辞但**同一个业务问题**（如"缩水"和"洗后变形"）
        SHOULD_NOT_MERGE 语义相近但**业务上是两件事**（如"面料透"和"面料扎人"）
    这两组才是真正的判据——簇数多少不代表好坏。

运行（在 backend/ 目录下）：
    python tests/check_embed_vs_tfidf.py
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import numpy as np                                    # noqa: E402
from sklearn.cluster import KMeans                    # noqa: E402
from sklearn.decomposition import TruncatedSVD        # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer   # noqa: E402
from sklearn.preprocessing import Normalizer          # noqa: E402

from hub.llm.embed import get_embedder                # noqa: E402
from hub.persistence.repo_cluster import ClusterRepo  # noqa: E402

# 业务上**应该**合到一起的（不同措辞，同一个问题）
SHOULD_MERGE = [
    ("掉色，洗一次就变旧了", "洗两次掉色严重，水都变色了"),
    ("缩水了，洗后小了一圈", "洗了一次就变形了"),
    ("开线了，穿几次就破了", "线头很多，做工粗糙"),
]

# 业务上**不该**合的（语义像，但是两件事）
SHOULD_NOT_MERGE = [
    ("面料很透，质量差", "面料扎人，穿着不舒服"),
    ("起球严重，面料太差", "掉色，洗一次就变旧了"),
    ("开线了，穿几次就破了", "纽扣掉了两颗，质量堪忧"),
]

MAX_PRINT_SAMPLES = 2      # 每个簇打印几条样例
SAMPLE_CHARS = 26


def _k(n: int) -> int:
    """子簇数，和 hub/analysis/cluster.py 的公式保持一致。"""
    return 1 if n < 20 else max(2, min(5, round(n / 40)))


def cluster_tfidf(texts: list[str], k: int) -> list[int]:
    """方案 A：TF-IDF 字符 2~3-gram → SVD(30) → L2 → KMeans。"""
    matrix = TfidfVectorizer(analyzer="char", ngram_range=(2, 3),
                             min_df=1, max_df=0.9).fit_transform(texts)
    n_comp = min(30, matrix.shape[1] - 1, len(texts) - 1)
    reduced = Normalizer().fit_transform(
        TruncatedSVD(n_components=n_comp, random_state=42).fit_transform(matrix))
    return KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(reduced).tolist()


def cluster_embed(texts: list[str], k: int, embedder) -> list[int]:
    """方案 B：bge-m3 向量（L2 归一化）→ KMeans。"""
    vecs = embedder.embed(texts)
    vecs = Normalizer().fit_transform(vecs)
    return KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(vecs).tolist()


def _template_label(texts: list[str], labels: list[int], template: str):
    """某模板的评论主要落在哪个簇（众数），返回 (簇号, 命中条数)。"""
    idxs = [i for i, t in enumerate(texts) if template in (t or "")]
    if not idxs:
        return None, 0
    counts = Counter(labels[i] for i in idxs)
    label, n = counts.most_common(1)[0]
    return label, len(idxs)


def main() -> None:
    rows = ClusterRepo().fetch_reviews()
    if not rows:
        print("review_topic 是空的，先跑 python -m hub.cli topic")
        return

    by_topic: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.topic != "other":
            by_topic[r.topic].append(r)

    print(f"差评 {len(rows)} 条，参与对比 {sum(len(v) for v in by_topic.values())} 条\n")

    embedder = get_embedder()
    print(f"embedding 模型：{embedder.name}（如果没拉过：ollama pull {embedder.name}）\n")

    results: dict[str, dict] = {}
    for topic in sorted(by_topic, key=lambda t: -len(by_topic[t])):
        items = by_topic[topic]
        texts = [r.text or "" for r in items]
        k = _k(len(texts))
        print(f"{'=' * 70}\n{topic}（{len(texts)} 条，k={k}）\n{'=' * 70}")

        for method, fn in (
            ("TF-IDF", lambda t, kk: cluster_tfidf(t, kk)),
            ("bge-m3", lambda t, kk: cluster_embed(t, kk, embedder)),
        ):
            labels = fn(texts, k)
            results[f"{method}:{topic}"] = {
                "texts": texts, "labels": labels, "items": items,
            }
            groups: dict[int, list[int]] = defaultdict(list)
            for i, lab in enumerate(labels):
                groups[lab].append(i)

            print(f"\n  【{method}】{len(groups)} 个簇")
            for lab, idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                samples = " | ".join(
                    texts[i][:SAMPLE_CHARS] for i in idxs[:MAX_PRINT_SAMPLES])
                print(f"    [{len(idxs):>3}] {samples}")

    # ---------- 定向检查：该合的合了吗？不该合的分开了吗 ----------
    print(f"\n{'=' * 70}\n定向检查（人工判定）\n{'=' * 70}")
    for title, pairs, want_same in (
        ("应该合并（同一问题的不同说法）", SHOULD_MERGE, True),
        ("不该合并（业务上是两件事）", SHOULD_NOT_MERGE, False),
    ):
        print(f"\n{title}")
        for a, b in pairs:
            line = f"  「{a[:14]}」 vs 「{b[:14]}」"
            for method in ("TF-IDF", "bge-m3"):
                # 找这两个模板所在的 topic
                target = None
                for topic in by_topic:
                    res = results.get(f"{method}:{topic}")
                    if not res:
                        continue
                    if any(a in t for t in res["texts"]) and any(b in t for t in res["texts"]):
                        target = res
                        break
                if target is None:
                    line += f"｜{method}: 找不到"
                    continue
                la, na = _template_label(target["texts"], target["labels"], a)
                lb, nb = _template_label(target["texts"], target["labels"], b)
                same = la is not None and la == lb
                ok = "✅" if same == want_same else "❌"
                line += f"｜{method}: {'同簇' if same else '不同簇'} {ok}({na}/{nb}条)"
            print(line)

    print("\n说明："
          "\n  · 应该合并的 → 期望『同簇』"
          "\n  · 不该合并的 → 期望『不同簇』"
          "\n  · 哪个方案 ✅ 更多，哪个更适合当前数据")


if __name__ == "__main__":
    main()
