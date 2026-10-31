# -*- coding: utf-8 -*-
"""
跨平台问题簇聚类（cluster.py）
=============================
把差评自动分成若干个"问题簇"，并用 LLM 给每簇起名。

**分层聚类：先按 M3 的 topic 分组，再在组内聚类。**

为什么分层（而不是全量一起聚）：
    实测全量 KMeans（k=10 或 15）会造出几个"垃圾桶簇"——
    比如一个 94 条的簇，里面 size 30 / misship 25 / quality 14，
    纯度只有 32%，但 LLM 照样给它起名"发货慢"，误导性极强。
    按 topic 分组后，每个簇天然同质（纯度普遍 85%+），
    业务上也更自然："质量问题里，开线占多少、起球占多少"。

算法（每个 topic 内部）：
    ① TF-IDF 字符 2~3-gram
    ② SVD 降维（稀疏高维空间里距离没有区分度）
    ③ KMeans，子簇数 = max(2, min(5, n/40))
    ④ 每簇取"距质心最近"的 ≤10 条喂 LLM 命名
    ⑤ 写 issue_cluster（按 run_id 隔离）
"""
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher      # 簇名相似度用编辑距离，不用 TF-IDF
from uuid import uuid4

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

from hub.core.tracing import traced
from hub.llm import TextLLM, get_text_llm
from hub.persistence.repo_cluster import ClusterRepo
from hub.persistence.repository import RunRepo

MIN_FOR_SPLIT = 20      # 一个 topic 少于这么多条，就不拆子簇
MAX_PER_TOPIC = 5       # 一个 topic 最多拆几个子簇
SAMPLE_PER_CLUSTER = 10  # 每簇取多少条喂 LLM 命名
REPS_PER_CLUSTER = 5     # 每簇存几条代表评价
MIN_CLUSTER_REVIEWS = 3  # 少于这么多条的簇直接丢弃（1~2 条没有统计意义）
MERGE_THRESHOLD = 0.80   # 簇名相似度 >= 该值就合并（"退运费不退" vs "退运费未退"）


@dataclass
class ClusterStats:
    """一次聚类的统计。"""
    total_reviews: int = 0
    topics: int = 0            # 参与聚类的 topic 数
    clusters: int = 0          # 子簇总数
    named: int = 0             # 成功命名的簇数
    wrote: int = 0             # 写入 issue_cluster 的行数
    members: int = 0           # 写入 cluster_member 的行数（簇 × 成员）
    sizes: list[int] = None    # 每个子簇的大小
    trace_id: str = ""
    elapsed_s: float = 0.0


def _name_cluster(llm: TextLLM, samples: list) -> str:
    """让 LLM 给一个子簇起名（2~10 个汉字，要具体）。"""
    listing = "\n".join(f"[R{i + 1}] {(r.text or '')[:100]}"
                        for i, r in enumerate(samples))
    prompt = (
        "你是电商运营分析助手。下面是同一类差评的若干条原文（已聚类到一起）。\n\n"
        f"{listing}\n\n"
        "请给这一类问题起一个**简短具体**的名字（2~10 个汉字）。\n"
        '例如："开线脱线"、"尺码偏小"、"物流太慢"、"色差明显"。\n'
        '不要用"质量问题"、"其他"这种笼统词。\n\n'
        "只输出名字本身，不要解释、不要引号、不要序号。"
    )
    try:
        raw = llm.chat(prompt, max_tokens=30)
    except Exception as e:                       # noqa: BLE001
        print(f"  [聚类] 命名失败：{e}")
        return "未命名"

    line = raw.strip().splitlines()[0] if raw.strip() else ""
    line = line.strip().strip('"\'“”「」《》')
    line = re.sub(r"^\s*\d+\s*[.、)）]\s*", "", line)
    line = line.strip("。.,，:：")
    return line[:20] or "未命名"


def _split_topic(rows: list, indices: list[int], k: int) -> list[list[int]]:
    """把一个 topic 内的评价聚成 k 个子簇，返回若干 index 列表。"""
    if k <= 1 or len(indices) < 4:
        return [indices]

    texts = [rows[i].text or "" for i in indices]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 3),
                                 min_df=1, max_df=0.9)
    matrix = vectorizer.fit_transform(texts)

    n_components = min(30, matrix.shape[1] - 1, len(indices) - 1)
    if n_components < 2:
        return [indices]
    reduced = Normalizer().fit_transform(
        TruncatedSVD(n_components=n_components, random_state=42).fit_transform(matrix)
    )

    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(reduced)

    groups: dict[int, list[int]] = defaultdict(list)
    for pos, label in enumerate(labels):
        groups[label].append(indices[pos])

    # 丢掉太小的组（低于 3 条不值得单独成簇），合并进最大的组
    small = [g for g in groups.values() if len(g) < 3]
    keep = [g for g in groups.values() if len(g) >= 3]
    if small:
        if keep:
            keep[0].extend(i for g in small for i in g)
        else:
            keep = [[i for g in small for i in g]]
    return keep


@traced("m4.cluster")
def run_clustering(
    *,
    max_per_topic: int = MAX_PER_TOPIC,
    llm: TextLLM | None = None,
) -> ClusterStats:
    """跑一次分层聚类：topic 分组 → 组内聚类 → LLM 命名 → 写 issue_cluster。"""
    t0 = time.time()
    repo = ClusterRepo()
    rows = repo.fetch_reviews()

    stats = ClusterStats(sizes=[])
    if not rows:
        print("没有可聚类的差评（review_topic 是空的）")
        stats.elapsed_s = round(time.time() - t0, 2)
        return stats

    stats.total_reviews = len(rows)

    # ---------- ① 按 topic 分组 ----------
    by_topic: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_topic[r.topic].append(i)
    stats.topics = len(by_topic)
    print(f"差评 {len(rows)} 条，分属 {stats.topics} 个 topic："
          f"{ {t: len(v) for t, v in sorted(by_topic.items(), key=lambda kv: -len(kv[1]))} }")

    # ---------- 创建 analysis_run ----------
    llm = llm or get_text_llm()
    trace_id = uuid4().hex
    run_repo = RunRepo()
    run_id = run_repo.create(trace_id, batch_id=None)
    stats.trace_id = trace_id

    # ---------- ② 逐 topic 聚类 + 命名 ----------
    cluster_rows = []
    for topic, indices in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        n = len(indices)
        # "other" 是"抱怨了但说不清具体问题"，把它们聚类没有业务意义
        # （实测：3 条 other 聚成"尺码不合"，反而是噪声）
        if topic == "other":
            print(f"\n[other] {n} 条 → 跳过（无法归类的评价不聚类）")
            continue
        # 子簇数：太小的组不拆；大的按 n/40 拆，最多 5 个
        k = 1 if n < MIN_FOR_SPLIT else max(2, min(max_per_topic, round(n / 40)))
        groups = _split_topic(rows, indices, k)
        print(f"\n[{topic}] {n} 条 → {len(groups)} 个子簇")

        for group in groups:
            # 距质心最近的若干条（用于命名）；这里用 TF-IDF 空间算
            picked = _pick_representatives(rows, group, SAMPLE_PER_CLUSTER)
            name = _name_cluster(llm, [rows[i] for i in picked])
            if name != "未命名":
                stats.named += 1

            # ✅ 已核实：**这里不用改**（债台账 #39 的结论在 2026-10-31 被推翻）
            #
            # 原来以为 bug：`business_domain` 存的是商品类目（女装/男装），
            #               而不是问题域（quality/logistics）。
            # 核实后：`contracts/analysis.py` 的字段注释写的是
            #         `business_domain: str  # 业务域（如"女装/连衣裙"）`
            #         → **品类就是设计本意** ✅ 代码是对的 ✅
            #
            # 真正的缺口是另一件事：**`issue_cluster` 没有"问题类型"这一列** ——
            #   因为一个簇可能跨多个问题类型（聚类是按文本相似度，不按 topic 分组），
            #   所以设计时刻意没加 ✅
            #   → 需要"问题类型"时，从 `cluster_member → review_topic.topic` 取众数
            #     （`scripts/probe_planning.py::load_tasks` 有现成写法 ✅
            #       阶段 3.1 的 `query_cluster` 也照它做 ✅）
            cats = [rows[i].category for i in group if rows[i].category]
            domain = Counter(cats).most_common(1)[0][0] if cats else "未知"
            platforms = sorted({rows[i].platform for i in group})
            sevs = [rows[i].severity for i in group if rows[i].severity is not None]
            severity = round(sum(sevs) / len(sevs) / 5, 3) if sevs else None

            reps = [rows[i].id for i in picked[:REPS_PER_CLUSTER]]

            cluster_rows.append({
                "run_id": run_id,
                "label": name,
                "business_domain": domain,
                "platforms": platforms,
                "review_count": len(group),
                "severity": severity,
                "representative_review_ids": reps,
                # 这个簇涉及的商品（优先级打分算 reach 要用）
                "spu_ids": sorted({rows[i].platform_spu_id for i in group
                                   if rows[i].platform_spu_id}),
                # 内部字段（下划线开头，不写 issue_cluster 表）：
                # 该簇的全部 record_id → 写 cluster_member，供 M6 回复/M9 复盘/M10 展开用
                "_members": [rows[i].id for i in group],
            })
            stats.sizes.append(len(group))
            print(f"  [{len(group):>3} 条] {name}（{domain}，{'/'.join(platforms)}）")

    # ---------- ③ 合并同名簇 + 过滤小簇 ----------
    before = len(cluster_rows)
    cluster_rows = _merge_similar(cluster_rows)
    cluster_rows = [r for r in cluster_rows
                    if r["review_count"] >= MIN_CLUSTER_REVIEWS]
    if len(cluster_rows) != before:
        print(f"\n合并同名簇 / 过滤小簇后：{before} → {len(cluster_rows)} 个")

    # ---------- ④ 写库 ----------
    stats.clusters = len(cluster_rows)
    stats.sizes = [r["review_count"] for r in cluster_rows]
    stats.wrote, stats.members = repo.insert_clusters(cluster_rows)
    stats.elapsed_s = round(time.time() - t0, 2)

    run_repo.update_status(trace_id, "DONE", stats={
        "module": "M4-cluster",
        "total_reviews": stats.total_reviews,
        "topics": stats.topics,
        "clusters": stats.clusters,
        "named": stats.named,
        "sizes": sorted(stats.sizes, reverse=True),
        "elapsed_s": stats.elapsed_s,
    })
    return stats


def _pick_representatives(rows: list, indices: list[int], n: int) -> list[int]:
    """取距质心最近的 n 条（用 TF-IDF 空间，簇内再算一次向量）。"""
    if len(indices) <= n:
        return indices
    texts = [rows[i].text or "" for i in indices]
    try:
        matrix = TfidfVectorizer(analyzer="char", ngram_range=(2, 3),
                                 min_df=1).fit_transform(texts)
    except ValueError:
        return indices[:n]
    centroid = np.asarray(matrix.mean(axis=0))
    sims = cosine_similarity(matrix, centroid).ravel()
    order = np.argsort(-sims)[:n]
    return [indices[k] for k in order]


def _merge_similar(rows: list[dict], threshold: float = MERGE_THRESHOLD) -> list[dict]:
    """把名字高度相似的簇合并。

    为什么需要：
        分层聚类是在**单个 topic 内部**做的，但同一个业务问题可能落在不同 topic 里。
        实测：[service] 的"退运费不退" 和 [misship] 的"退运费未退" 就是同一个问题。

    为什么用**编辑距离**而不是 TF-IDF 余弦：
        簇名只有 4~6 个字，TF-IDF 会把共享字（"退""运""费"）当成高频词压低权重，
        实测余弦只有 0.62，合不上；
        编辑距离只看"改几个字"："退运费不退/退运费未退" = 0.80（该合），
        "尺码不准/尺码不符" = 0.75（不该合，一个是尺码表不准，一个是发错码）。

    :return: 合并后的簇列表（review_count / platforms / spu_ids / severity 都重新聚合）
    """
    if len(rows) < 2:
        return rows

    # 并查集
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            ratio = SequenceMatcher(None, rows[i]["label"], rows[j]["label"]).ratio()
            if ratio >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(rows)):
        groups[find(i)].append(i)

    merged: list[dict] = []
    for idxs in groups.values():
        if len(idxs) == 1:
            merged.append(dict(rows[idxs[0]]))
            continue

        # 以成员最多的那个为准（保留它的名字和品类）
        base = max(idxs, key=lambda i: rows[i]["review_count"])
        out = dict(rows[base])
        out["review_count"] = sum(rows[i]["review_count"] for i in idxs)
        out["platforms"] = sorted({p for i in idxs for p in rows[i]["platforms"]})
        out["spu_ids"] = sorted({s for i in idxs for s in rows[i].get("spu_ids", [])})
        out["representative_review_ids"] = [
            rid for i in idxs for rid in rows[i]["representative_review_ids"]
        ][:REPS_PER_CLUSTER]
        # 成员也要合并，否则 cluster_member 会漏掉被合并进来的那部分
        out["_members"] = [m for i in idxs for m in (rows[i].get("_members") or [])]
        weighted = [(rows[i]["severity"], rows[i]["review_count"])
                    for i in idxs if rows[i]["severity"] is not None]
        if weighted:
            out["severity"] = round(
                sum(s * w for s, w in weighted) / sum(w for _, w in weighted), 3)

        names = " + ".join(rows[i]["label"] for i in idxs)
        print(f"  🔗 合并同名簇：{names} → {out['label']}（{out['review_count']} 条）")
        merged.append(out)

    return merged
