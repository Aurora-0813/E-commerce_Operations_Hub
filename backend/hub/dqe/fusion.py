# -*- coding: utf-8 -*-
"""
决策融合（fusion.py）
====================
把"规则命中"和"ML 分数"合成最终判定（DQEVerdict）。

优先级（从高到低）：
    ① 硬规则命中        → drop（差评降级 review；R008 注入除外）
    ② ML 水军分 ≥ 阈值   → drop（差评降级 review；差评阈值更高）
    ③ 疑似刷好评         → review（异常分高 + 高分评价）
    ④ 都没命中          → keep

差评保护（R009，设计文档 §3.1.4）：
    rating ≤ 2 时，规则轨和 ML 轨给出的 drop 一律降级为 review。
    理由：误杀真实差评的代价远高于多花 token。
    唯一例外：R008（Prompt 注入）——为阻断攻击面，差评也硬 drop。
"""
from hub.contracts import DQEVerdict
from hub.dqe.rules import RuleHit


def decide(
    *,
    review_id: str,
    rating: int,
    rule_hits: list[RuleHit],
    spam_score: float,
    anomaly_score: float = 0.0,
    engine: str = "hybrid",
    model_version: str | None = None,
    spam_threshold: float = 0.75,
    spam_threshold_negative: float = 0.90,
    anomaly_threshold: float = 0.72,
) -> DQEVerdict:
    """做一条评价的最终判定。

    :param review_id:               评价 ID
    :param rating:                  评分 1~5（R009 差评保护看它）
    :param rule_hits:               rules.check_item() 的结果
    :param spam_score:              ML 水军概率 0~1（降级时为 0）
    :param anomaly_score:           异常分 0~1（第一版恒为 0）
    :param engine:                  "hybrid" / "rule_only"
    :param model_version:           模型版本（rule_only 时为空）
    :param spam_threshold:          ML 判定阈值（非差评）
    :param spam_threshold_negative: ML 判定阈值（差评，更高=更宽松）
    :param anomaly_threshold:       异常分阈值
    """
    # 引擎标签：ml 是否真的在工作
    engine_label = "ml+rule" if engine == "hybrid" else "rule_only"

    def build(decision: str, reason: str) -> DQEVerdict:
        return DQEVerdict(
            review_id=review_id,
            decision=decision,          # type: ignore[arg-type]
            reason_code=reason,
            spam_score=spam_score,
            anomaly_score=anomaly_score,
            hit_rules=[h.rule_id for h in rule_hits],
            engine=engine_label,        # type: ignore[arg-type]
            model_version=model_version,
        )

    # ---------- ① 硬规则优先 ----------
    if rule_hits:
        first = rule_hits[0]                       # 最具体的规则（rules.py 已排序）
        # 只要有一条"不可降级"的规则（R008），差评也不降级
        has_undowngradable = any(not h.downgradable for h in rule_hits)
        downgrade = rating <= 2 and not has_undowngradable
        return build("review" if downgrade else "drop", first.rule_id)

    # ---------- ② ML 水军判定 ----------
    # 差评用更高的阈值：宁放过，不误杀
    threshold = spam_threshold_negative if rating <= 2 else spam_threshold
    if spam_score >= threshold:
        # 差评即使超过阈值，也只标 review，不 drop
        return build("review" if rating <= 2 else "drop", "ML_SPAM")

    # ---------- ③ 疑似刷好评 ----------
    # 第一版 anomaly_score 恒为 0，这段暂时不会触发；保留以对齐设计文档
    if anomaly_score >= anomaly_threshold and rating >= 4:
        return build("review", "SUSPECT_FAKE_POSITIVE")

    # ---------- ④ 正常 ----------
    return build("keep", "OK")
