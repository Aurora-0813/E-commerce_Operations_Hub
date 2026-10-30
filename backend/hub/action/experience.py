# -*- coding: utf-8 -*-
"""
动作经验回灌（action/experience.py）
====================================
**这是把 M9 的复盘结论接回 M5 决策的那一环。**

    没有它，系统是"跑得通的流水线"：
        每个月照样出提案，但不知道上次这么做过没有、有没有用。
    有它之后，系统才会：
        · 对"历史上有害"的做法**提高审批级别**（L1 → L2）
        · 对"历史上有效"的做法**优先选择**
        · 样本不够时**什么都不改**（只做提示）

**三条设计原则**（都是踩出来的，不是拍脑袋）：

    ① 收紧要快、放开要慢（门槛不对称：负向 2 次，正向 3 次）
       规则 1 错了的代价是"多让人看一眼"；规则 2 错了的代价是
       "自动做了一件没用甚至有害的事"。两者不对称，门槛就该不对称。

    ② INCONCLUSIVE 的结论**不计入样本**
       撞大促 / 重叠干预 / 变化不显著 —— 它们【没有告诉我们工具好不好】。
       如果把 INCONCLUSIVE 当成"失败"，会误伤一个可能有效的工具。

    ③ 样本不足时**只提示、不改行为**
       1 次 NEGATIVE 不足以下结论（可能只是撞上了大促）。
       小于噪声的差异不能改变行为——和 M9 的显著性判定是同一个思路。

**为什么这一层值得单独写**：
    它改的是"决策"，不是"数据"。改错了会**悄悄地让系统变坏**
    （比如错误地不再自动回复，或者错误地优先用某个工具），
    所以它必须独立可测（见 tests/check_experience.py）。
"""
from dataclasses import dataclass, field

from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import (
    ActionOutcome,
    ActionProposal,
    ClusterMember,
    ReviewTopic,
)

# ---- 门槛（改这两个数要同时改文档和测试）----
NEGATIVE_THRESHOLD = 2     # 负向：≥2 次就收紧（收紧要快）
POSITIVE_THRESHOLD = 3     # 正向：≥3 次才放开（放开要慢）


@dataclass
class ToolRecord:
    """某个 (topic, 工具) 组合的历史战绩。"""
    tool: str
    positive: int = 0
    negative: int = 0
    inconclusive: int = 0
    pending: int = 0          # 还在等观察窗口的（不计入任何门槛）

    @property
    def decided(self) -> int:
        """有明确结论的次数（POSITIVE + NEGATIVE）。"""
        return self.positive + self.negative

    def summary(self) -> str:
        return (f"{self.tool}：有效 {self.positive} 次／有害 {self.negative} 次"
                f"／结论不明 {self.inconclusive} 次"
                + (f"／待观察 {self.pending} 次" if self.pending else ""))


@dataclass
class Advice:
    """给提案器的一条建议。"""
    topic: str
    records: list[ToolRecord] = field(default_factory=list)
    prefer_tool: str | None = None      # 建议优先使用的工具（有充分正面证据）
    escalate: bool = False              # 是否要求提高审批级别（有负面证据）
    escalate_tool: str | None = None
    reason: str = ""                    # 人话理由（写进提案的 rationale）

    @property
    def has_advice(self) -> bool:
        return self.prefer_tool is not None or self.escalate


def topic_by_cluster() -> dict[str, str]:
    """每个簇的【主导 topic】（成员里出现最多的那个）。

    和 `proposer._dominant_topic` 同一口径——两处口径必须一致，
    否则"经验"会按 A 类问题统计、却用在 B 类问题上。
    """
    with get_session() as s:
        rows = s.execute(
            select(ClusterMember.cluster_id, ReviewTopic.topic, func.count())
            .join(ReviewTopic, ReviewTopic.record_id == ClusterMember.record_id)
            .group_by(ClusterMember.cluster_id, ReviewTopic.topic)
        ).all()
    best: dict[str, tuple[str, int]] = {}
    for cid, topic, n in rows:
        key = str(cid)
        if key not in best or n > best[key][1]:
            best[key] = (topic, n)
    return {k: v[0] for k, v in best.items()}


def history_for(topic: str) -> list[ToolRecord]:
    """查某个 topic 下，各工具的历史战绩。

    **按提案去重，不按 outcome 计数**：
        一个提案可能有多条 outcome（一个受影响商品一条），但它们的结论相同。
        如果按 outcome 数，一个覆盖 40 个商品的动作会被算 40 次，
        权重远大于只覆盖 1 个商品的动作——那不是"证据更多"，是"商品更多"。
    """
    cluster_topic = topic_by_cluster()
    wanted = {cid for cid, t in cluster_topic.items() if t == topic}
    if not wanted:
        return []

    records: dict[str, ToolRecord] = {}
    with get_session() as s:
        proposals = s.scalars(select(ActionProposal)).all()
        # 一个提案取一条 outcome 的结论即可（再按 conclusion 分类）
        concl_by_proposal: dict = {}
        for pid, concl, status in s.execute(
                select(ActionOutcome.proposal_id, ActionOutcome.conclusion,
                       ActionOutcome.status)).all():
            if pid in concl_by_proposal:
                continue
            concl_by_proposal[pid] = (concl, status)

        for p in proposals:
            args = p.args if isinstance(p.args, dict) else {}

            # 优先读提案自己记的 topic（生成时就知道，最可靠）
            #
            # 为什么不能只靠 cluster_id 反推：
            #   `listing.update_desc` 的参数是 spu_id，**没有 cluster_id**，
            #   反推不出来 → 改描述的历史结论会整类丢失。
            #   （实测踩过：4 个历史动作全是 spu 级，经验回灌一个都看不见。）
            own_topic = args.get("topic")
            if own_topic:
                if own_topic != topic:
                    continue
            else:
                cid = str(args.get("cluster_id") or "")
                if cid not in wanted:
                    continue

            rec = records.setdefault(p.tool_name, ToolRecord(tool=p.tool_name))
            concl, status = concl_by_proposal.get(p.id, (None, None))
            if status != "DONE":
                rec.pending += 1
            elif concl == "POSITIVE":
                rec.positive += 1
            elif concl == "NEGATIVE":
                rec.negative += 1
            else:
                rec.inconclusive += 1
    return sorted(records.values(), key=lambda r: -r.decided)


def decide(records: list[ToolRecord], topic: str,
           default_tool: str) -> Advice:
    """**纯函数**：只根据历史战绩给建议，不碰数据库。

    为什么拆出来：这一层改的是"决策"，改错了会悄悄让系统变坏。
        而"验证决策规则"必须能**构造数据**（"如果有 3 次 POSITIVE 会怎样？"），
        不能靠往真库里插假数据。
        拆成纯函数之后，规则可以被穷举测试（见 tests/check_experience_rules.py）。
    """
    adv = Advice(topic=topic, records=records)
    if not records:
        adv.reason = f"该问题类型暂无历史动作（默认规则：{default_tool}）"
        return adv

    # 规则 1：负向 → 收紧（门槛 2）
    for r in records:
        if r.negative >= NEGATIVE_THRESHOLD and r.negative > r.positive:
            adv.escalate = True
            adv.escalate_tool = r.tool
            adv.reason = (f"「{r.tool}」处理此类问题 {r.decided} 次，"
                          f"其中 {r.negative} 次导致差评率上升 → 提高审批级别")
            return adv

    # 规则 2：正向 → 优先（门槛 3，且不能有 INCONCLUSIVE 混着）
    for r in records:
        if (r.positive >= POSITIVE_THRESHOLD and r.negative == 0
                and r.inconclusive == 0):
            adv.prefer_tool = r.tool
            adv.reason = (f"「{r.tool}」处理此类问题 {r.positive} 次，"
                          f"全部有效 → 优先使用")
            return adv

    # 证据不足：只提示，不改行为
    best = max(records, key=lambda r: r.decided)
    adv.reason = (f"历史证据不足，不改行为（{best.summary()}；"
                  f"正向需 {POSITIVE_THRESHOLD} 次／负向需 {NEGATIVE_THRESHOLD} 次）")
    return adv


def advise(topic: str, default_tool: str) -> Advice:
    """查库 + 判规则。真正的逻辑在 `decide()` 里（纯函数，好测）。"""
    return decide(history_for(topic), topic, default_tool)
