# -*- coding: utf-8 -*-
"""
跨平台同款对齐（align.py）
=========================
把"淘宝的 TB-0001"和"京东的 JD-0001"认成同一个商品，生成 `canonical_spu_id`。

为什么需要：
    M4 的价格背离、跨平台聚类都以"同一个商品"为单位。
    没有对齐，就只能单平台分析，"跨平台中枢"名不副实。

算法（四步）：
    ① 归一化标题：去品牌、去空格标点、转小写
    ② TF-IDF 字符 2~3-gram 向量
    ③ 余弦相似度矩阵
    ④ 贪心一对一匹配：
         sim >= 0.85             → 自动匹配（source=match）
         0.60 <= sim < 0.85      → 进人工候选队列（**不写入**，只报告）
         sim < 0.60              → 不匹配
    ⑤ 分配 canonical_spu_id：
         匹配上的两个平台共用同一个 ID（SPU-0001）
         没匹配上的各自独占一个 ID（SPU-0021…）

为什么不改 spu_catalog：
    它现在存的是"平台商品"（PK 叫 canonical_spu_id，但值是 TB-0001，名不副实）。
    改成真正的 canonical 表要合并行 + 改外键，风险高、收益低。
    所有跨平台查询用 `JOIN sku_mapping` 就能解决。
"""
import re
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 品牌词：归一化时去掉（品牌对"是不是同一个商品"没有区分度）
BRAND_WORDS = ("森马", "semir")

# 标点/空白：归一化时去掉
_PUNCT = re.compile(r"[\s\-_/\\|,，。.、;；:：!！?？()（）\[\]【】\"'“”‘’]+")

AUTO_MATCH = 0.85     # 自动匹配阈值
REVIEW_GATE = 0.60    # 人工候选阈值


def normalize_title(title: str) -> str:
    """归一化商品标题。

    "森马 夏季碎花连衣裙收腰显瘦中长款" → "夏季碎花连衣裙收腰显瘦中长款"
    """
    s = str(title or "").lower()
    for brand in BRAND_WORDS:
        s = s.replace(brand, "")
    return _PUNCT.sub("", s)


@dataclass
class AlignResult:
    """一次对齐的结果。"""
    mappings: list[dict] = field(default_factory=list)   # 写进 sku_mapping 的行
    matched_pairs: int = 0          # 自动匹配对数
    review_pairs: list[tuple] = field(default_factory=list)   # 待人工确认的候选
    tb_only: int = 0                # 淘宝独占
    jd_only: int = 0                # 京东独占
    canonical_total: int = 0        # 生成了多少个 canonical_spu_id


def align_products(
    tb_products: list[dict],
    jd_products: list[dict],
    *,
    auto: float = AUTO_MATCH,
    review_gate: float = REVIEW_GATE,
) -> AlignResult:
    """对齐两个平台的商品。

    :param tb_products: [{"spu_id": "TB-0001", "title": "..."}]
    :param jd_products: [{"spu_id": "JD-0001", "title": "..."}]
    :param auto:        自动匹配阈值（>= 该值直接写入）
    :param review_gate: 人工候选阈值（>= 该值进候选队列，不写入）
    """
    result = AlignResult()
    if not tb_products or not jd_products:
        return result

    tb_titles = [normalize_title(p["title"]) for p in tb_products]
    jd_titles = [normalize_title(p["title"]) for p in jd_products]

    # ---- ② TF-IDF 字符 2~3-gram（中文短语用字 n-gram 比词更稳）----
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), min_df=1)
    matrix = vectorizer.fit_transform(tb_titles + jd_titles)
    tb_vec = matrix[:len(tb_titles)]
    jd_vec = matrix[len(tb_titles):]

    # ---- ③ 相似度矩阵（形状：淘宝数 × 京东数）----
    sim = cosine_similarity(tb_vec, jd_vec)

    # ---- ④ 候选对按相似度降序，贪心一对一 ----
    candidates = []
    for i in range(len(tb_products)):
        for j in range(len(jd_products)):
            s = float(sim[i, j])
            if s >= review_gate:
                candidates.append((s, i, j))
    candidates.sort(reverse=True)

    used_tb: set[int] = set()
    used_jd: set[int] = set()
    matched: list[tuple[float, int, int]] = []

    for s, i, j in candidates:
        if i in used_tb or j in used_jd:
            continue                      # 一对一：已经配对过就跳过
        if s >= auto:
            used_tb.add(i)
            used_jd.add(j)
            matched.append((s, i, j))
        else:
            # 只在"双方都还没配对"时记入人工候选，避免候选爆炸
            result.review_pairs.append((s, i, j, tb_titles[i], jd_titles[j]))

    # ---- ⑤ 分配 canonical_spu_id ----
    cid = 1
    for s, i, j in matched:
        canonical = f"SPU-{cid:04d}"
        cid += 1
        for platform, spu in (("taobao", tb_products[i]["spu_id"]),
                              ("jd", jd_products[j]["spu_id"])):
            result.mappings.append({
                "canonical_spu_id": canonical,
                "platform": platform,
                "platform_spu_id": spu,
                "source": "match",
                "confidence": round(s, 3),
            })
    result.matched_pairs = len(matched)

    # 没配对的商品：各自独占一个 canonical（source=rule，confidence=1.0）
    for i, p in enumerate(tb_products):
        if i not in used_tb:
            result.mappings.append({
                "canonical_spu_id": f"SPU-{cid:04d}",
                "platform": "taobao",
                "platform_spu_id": p["spu_id"],
                "source": "rule",
                "confidence": 1.0,
            })
            cid += 1
            result.tb_only += 1

    for j, p in enumerate(jd_products):
        if j not in used_jd:
            result.mappings.append({
                "canonical_spu_id": f"SPU-{cid:04d}",
                "platform": "jd",
                "platform_spu_id": p["spu_id"],
                "source": "rule",
                "confidence": 1.0,
            })
            cid += 1
            result.jd_only += 1

    result.canonical_total = cid - 1
    return result


def run_alignment() -> AlignResult:
    """读商品目录 → 对齐 → 写入 sku_mapping。

    这是 M11 的"同款对齐"入口，M4 之前必须跑一次。
    """
    from hub.persistence.repo_sku import SkuRepo

    repo = SkuRepo()
    catalog = repo.fetch_catalog()
    if not catalog:
        raise RuntimeError("spu_catalog 是空的，或 platform 全为 NULL")

    tb = [{"spu_id": r.canonical_spu_id, "title": r.title}
          for r in catalog if r.platform == "taobao"]
    jd = [{"spu_id": r.canonical_spu_id, "title": r.title}
          for r in catalog if r.platform == "jd"]

    result = align_products(tb, jd)
    repo.upsert_many(result.mappings)
    return result
