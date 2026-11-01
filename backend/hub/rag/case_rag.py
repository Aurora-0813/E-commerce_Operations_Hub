# -*- coding: utf-8 -*-
"""
Case RAG（case_rag.py）—— 阶段 3.5 的 ②③
========================================
路线图 §8.3：

| 项 | 方案 |
| --- | --- |
| 相似度 | **TF-IDF + 余弦**（复用 M4）→ 案例库只有几十条，向量没优势 |
| 过滤 | 先按 `topic` 过滤，再算相似度 |
| Top-K | 3 条 |
| 注入 | rationale 里附"参考案例"，**每条引用必须存在**、数字必须在 effect 里找得到；
        校验失败**只丢该引用** |

**当前数据状态（2026-11-01）**：
    案例库是**空的** ✅ —— 因为真实干预还在观察窗口里（最早 2026-11-13）。
    → 所以本模块的"检索不到"是**正常路径**，返回空列表 ✅
    → 调用方（adopt）在没有案例时**不注入**，不编造"参考案例" ✅

**为什么不存向量**：
    `case_entry.embedding` 字段留着但为 NULL ✅
    几十条案例用 TF-IDF 现算更快、更好调试 ✅
    （路线图 §8.3 说 A/B 后再换 bge-m3 ✅）
"""
from __future__ import annotations

import math
import re
from collections import Counter

TOP_K = 3
MIN_SIMILARITY = 0.05     # 低于这个不算命中（TF-IDF 的特征词很少，门槛别太高 ✅）


def _tokens(text: str) -> list[str]:
    """中英混排的粗分词（**不引入 jieba**：案例文本短，二元切就够 ✅）。

    · 中文：按 2 字滑窗（"掉色严重" → 掉色/色严/严重）✅
    · 英文/数字：按词 ✅
    """
    t = (text or "").lower()
    words = re.findall(r"[a-z0-9_]+", t)
    zh = re.sub(r"[a-z0-9_]+", " ", t)
    zh_chars = re.findall(r"[\u4e00-\u9fff]", zh)
    for i in range(len(zh_chars) - 1):
        words.append(zh_chars[i] + zh_chars[i + 1])
    return words


def _tfidf_vectors(docs: list[list[str]]) -> list[dict[str, float]]:
    """手写 TF-IDF（不装 sklearn 的那套，**只为一件事**：案例几十条时它更透明 ✅）。"""
    n = len(docs)
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    idf = {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}
    vecs = []
    for d in docs:
        tf = Counter(d)
        total = sum(tf.values()) or 1
        vecs.append({w: (c / total) * idf.get(w, 1.0) for w, c in tf.items()})
    return vecs


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(w, 0.0) for w, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def retrieve(query_text: str, *, topic: str | None = None,
             k: int = TOP_K) -> list[dict]:
    """检索相似案例。返回按 `rank_score` 降序的列表（**可能为空** ✅）。

    :param query_text: 查询文本（簇名 + 代表评价 + 根因 ✅）
    :param topic:      先按问题类型过滤（路线图 §8.3 ✅）
    :param k:          返回几条（默认 3 ✅）

    `rank_score = 0.6 × 相似度 + 0.4 × |effect_size| 归一化` ✅
        （契约 `RetrievedCase.rank_score` 的注释就是这么写的 ✅）
        含义：既要"像这个问题"，又要"效果明显" ✅
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import CaseEntry

    with get_session() as s:
        stmt = select(CaseEntry).where(CaseEntry.status == "ACTIVE")
        if topic:
            stmt = stmt.where(CaseEntry.root_cause == topic)
        rows = s.scalars(stmt).all()
        cases = [{
            # ⚠️ `case_id` 从 DB 出来是 **UUID 对象** ✅
            #    而 `verify_citations` / 模型引用都是**字符串** ✅
            #    → 这里统一转 str，**在源头修**（不然每个调用方都要记得转 ✅）
            "case_id": str(c.case_id), "cluster_label": c.cluster_label,
            "root_cause": c.root_cause, "business_domain": c.business_domain,
            "action_taken": c.action_taken,
            "outcome_conclusion": c.outcome_conclusion,
            "effect_size": c.effect_size,
            "representative_text": c.representative_text,
        } for c in rows]

    if not cases:
        return []          # **空库是正常路径** ✅（不是错误 ✅）

    docs = [_tokens(c["representative_text"] + " " + (c["cluster_label"] or "")
                    + " " + (c["action_taken"] or "")) for c in cases]
    qvec = _tfidf_vectors(docs + [_tokens(query_text)])[-1]
    dvecs = _tfidf_vectors(docs)
    sims = [_cosine(qvec, v) for v in dvecs]

    # effect 归一化：用当前候选里的最大绝对值 ✅
    # ⚠️ `case_entry.effect_size` 是 **NUMERIC** → psycopg 返回 `Decimal` ✅
    #    `float * Decimal` 会 TypeError ✅ ✗ → 统一转 float ✅
    effs = [abs(float(c["effect_size"])) for c in cases
            if c["effect_size"] is not None]
    max_eff = max(effs, default=0.0) or 1.0
    out = []
    for c, sim in zip(cases, sims):
        if sim < MIN_SIMILARITY:
            continue
        eff = (abs(float(c["effect_size"])) if c["effect_size"] is not None else 0.0) / max_eff
        c2 = dict(c, similarity=round(sim, 4),
                  rank_score=round(0.6 * sim + 0.4 * eff, 4))
        out.append(c2)
    out.sort(key=lambda x: x["rank_score"], reverse=True)
    return out[:k]


def render_cases(cases: list[dict]) -> str:
    """把检索结果渲染成 prompt 里的"参考案例"块（**没有案例就返回空串** ✅）。

    ⚠️ 每条都带 `case_id` —— 这样模型引用时能对得上 ✅
       而 `verify_citations()` 会校验"引用的 id 真的在检索结果里" ✅
    """
    if not cases:
        return ""
    lines = ["参考案例（**只能引用下面的 case_id**）："]
    for i, c in enumerate(cases, 1):
        eff = c.get("effect_size")
        eff_s = f"{eff:+.4f}" if isinstance(eff, (int, float)) else "未知"
        lines.append(
            f"  [C{i}] {c['case_id']}｜{c['cluster_label']}｜"
            f"{c['outcome_conclusion']}（效果 {eff_s}）｜当时做了：{c['action_taken']}"
        )
    return "\n".join(lines)


def verify_citations(cited: list[str], cases: list[dict]) -> tuple[list[str], list[str]]:
    """校验模型引用的 case_id 是否真的在检索结果里。

    :return: (有效的, 编造的)
    路线图 §8.3："校验失败**只丢该引用**" ✅ —— 不整条作废 ✅
    """
    valid_ids = {c["case_id"] for c in cases}
    ok = [c for c in cited if c in valid_ids]
    bad = [c for c in cited if c not in valid_ids]
    return ok, bad
