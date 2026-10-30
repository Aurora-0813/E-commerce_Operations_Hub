# -*- coding: utf-8 -*-
"""
评测 M3 分类质量（对 gold_labels 的人工标注）
============================================
gold_labels.csv 的"主题分类"列扮演**人工标注**（ground truth），
拿它和 review_topic 里的模型预测比。

只评"问题类"的 6 个主题：
    quality / size / logistics / service / desc_mismatch / misship
（gold 里的 positive / other 不评——我们只分类差评）

重要说明：
    mock 的差评是模板生成的，文本规整、关键词明显，
    准确率会**虚高**；真实电商评论预计显著更低。
    这里的数字只用于验证"链路正确"，不代表模型真实能力。

运行（在 backend/ 目录下）：
    python scripts/eval_topic.py
"""
import csv
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402
from hub.persistence.models import ReviewTopic         # noqa: E402

GOLD_PATH = _BACKEND_DIR.parent / "data" / "mock" / "gold_labels.csv"
PROBLEM_TOPICS = ("quality", "size", "logistics", "service", "desc_mismatch", "misship")


def load_gold() -> dict[str, tuple[str, str]]:
    """读人工标注：{平台评价ID: (主主题, 次要主题)}，只保留问题类。

    ⚠️ 为什么要有次要主题：
        mock 里约 20% 的差评**同时抱怨两个问题**（"发货慢…，版型也有问题"）。
        如果只拿主主题去评，模型抓到了真实的第二个问题也会被判错——
        那是**指标错了**，不是模型错了。实测踩过：加了次要主题之后，
        表面准确率从 95.8% 掉到 82.4%，其中一大半是这个原因。
    """
    out: dict[str, tuple[str, str]] = {}
    with open(GOLD_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid = (row.get("记录ID") or "").strip()
            topic = (row.get("主题分类") or "").strip()
            second = (row.get("次要主题") or "").strip()
            if rid and topic in PROBLEM_TOPICS:
                out[rid] = (topic, second)
    return out


def load_pred(gold_ids: list[str]) -> dict[str, tuple[str, list[str]]]:
    """从数据库取模型的预测：{平台评价ID: (主主题, 次要主题列表)}。"""
    if not gold_ids:
        return {}
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_review_id, ReviewTopic.topic,
                   ReviewTopic.secondary_topics)
            .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
            .where(RecordRow.platform_review_id.in_(gold_ids))
        ).all()
    return {r.platform_review_id: (r.topic, list(r.secondary_topics or []))
            for r in rows}


def _label_set(primary: str, secondary) -> set[str]:
    """把"主 + 次"合成一个集合，用于"命中任一即算对"的判断。"""
    s = {primary} if primary else set()
    if secondary:
        if isinstance(secondary, str):
            s.add(secondary)
        else:
            s |= set(secondary)
    s.discard("")
    return s


def load_texts(gold_ids: list[str]) -> dict[str, str]:
    """取原文，用来在错例里展示"为什么模型会判错"。"""
    if not gold_ids:
        return {}
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_review_id, RecordRow.content_clean)
            .where(RecordRow.platform_review_id.in_(gold_ids))
        ).all()
    return {r.platform_review_id: (r.content_clean or "") for r in rows}


def main() -> None:
    gold = load_gold()
    print(f"人工标注（问题类）: {len(gold)} 条")

    pred = load_pred(list(gold))
    print(f"其中已分类的      : {len(pred)} 条")

    common = sorted(set(gold) & set(pred))
    if not common:
        print("\n❌ 没有交集——检查 gold 的『记录ID』和 record.platform_review_id 是否对得上")
        return
    print(f"可评测            : {len(common)} 条\n")
    texts = load_texts(common)

    # ---------- 准确率（集合重叠：命中主标签或次标签都算对）----------
    #
    # 为什么不是"主标签相等"：mock 里 20% 的差评同时抱怨两个问题，
    # 模型抓到第二个也是对的。用集合重叠才是公平的口径。
    correct = 0
    ml_hit, ml_total = 0, 0
    for k in common:
        g_set = _label_set(*gold[k])
        p_set = _label_set(*pred[k])
        if g_set & p_set:
            correct += 1
        # 多标签专项：人工标了次要主题的，模型有没有也抓到
        if gold[k][1]:
            ml_total += 1
            if gold[k][1] in p_set:
                ml_hit += 1
    acc = correct / len(common)
    print(f"=== 整体准确率（集合重叠）：{correct}/{len(common)} = {acc:.1%} ===\n")
    if ml_total:
        print(f"=== 多标签：人工标了次要主题的 {ml_total} 条，"
              f"模型抓到 {ml_hit} 条 = {ml_hit / ml_total:.1%} ===\n")

    # ---------- 每类 P/R/F1（按集合算）----------
    print(f"{'主题':<15} {'支持':>4} {'精确':>6} {'召回':>6} {'F1':>6}")
    print("-" * 44)
    for t in PROBLEM_TOPICS:
        tp = sum(1 for k in common if t in _label_set(*gold[k])
                 and t in _label_set(*pred[k]))
        fp = sum(1 for k in common if t not in _label_set(*gold[k])
                 and t in _label_set(*pred[k]))
        fn = sum(1 for k in common if t in _label_set(*gold[k])
                 and t not in _label_set(*pred[k]))
        support = tp + fn
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        print(f"{t:<15} {support:>4} {p:>6.2f} {r:>6.2f} {f1:>6.2f}")

    # ---------- 混淆矩阵（主标签，看"系统性混淆"）----------
    print("\n=== 混淆矩阵（行=人工主标签，列=模型主标签）===")
    header = "".join(f"{t[:8]:>10}" for t in PROBLEM_TOPICS)
    print(f"{'':<15}{header}")
    for g in PROBLEM_TOPICS:
        cells = []
        for p_ in PROBLEM_TOPICS:
            n = sum(1 for k in common if gold[k][0] == g and pred[k][0] == p_)
            cells.append(f"{n:>10}")
        print(f"{g:<15}{''.join(cells)}")

    # ---------- 错例：按"真实 → 预测"分组 + 带原文 ----------
    #
    # 为什么必须分组、必须给原文：
    #   只列 "ID + 人工=size 模型=quality" 看不出【为什么会错】。
    #   按 (真实, 预测) 对分组之后，"哪两类在系统性互相混淆" 一眼可见——
    #   那才是能改提示词的信号（比如 desc_mismatch 频繁被判成 misship，
    #   说明两个类目的定义边界不清，而不是模型笨）。
    # 判错 = 人工的标签集合 和 模型的标签集合【完全不相交】
    wrong = [(k, gold[k][0], pred[k][0]) for k in common
             if not (_label_set(*gold[k]) & _label_set(*pred[k]))]
    print(f"\n=== 判错 {len(wrong)} 条，按【真实→预测】分组===")

    pairs: dict[tuple[str, str], list[str]] = {}
    for rid, g, p_ in wrong:
        pairs.setdefault((g, p_), []).append(rid)
    for (g, p_), ids in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {g} → {p_}   {len(ids)} 条")
        for rid in ids[:2]:                    # 每组抽 2 条看原文
            print(f"    原文：{texts.get(rid, '（未取到）')[:70]}")
            print(f"    ID：{rid}")

    if not wrong:
        print("  （零错误）")

    # ---------- 错因归类（规则化的，不是拍脑袋）----------
    print("\n=== 错因归类 ===")
    reasons = Counter()
    for (g, p_) in pairs:
        if {g, p_} <= {"desc_mismatch", "misship"}:
            reasons["语义重叠：描述不符 vs 发错货"] += len(pairs[(g, p_)])
        elif {g, p_} <= {"quality", "size"}:
            reasons["语义重叠：质量问题 vs 尺码问题"] += len(pairs[(g, p_)])
        elif {g, p_} <= {"logistics", "service"}:
            reasons["语义重叠：物流 vs 客服"] += len(pairs[(g, p_)])
        else:
            reasons[f"其他：{g} ↔ {p_}"] += len(pairs[(g, p_)])
    for k, v in reasons.most_common():
        print(f"  {v:>3} 条  {k}")

    # ---------- 诚实提示 ----------
    print("\n" + "=" * 52)
    print("注意：mock 差评是模板生成的，文本规整，准确率虚高。")
    print("      真实电商评论（口语/错别字/无关键词）预计 70~80%。")
    print("      此数字仅用于验证链路正确性。")


if __name__ == "__main__":
    main()
