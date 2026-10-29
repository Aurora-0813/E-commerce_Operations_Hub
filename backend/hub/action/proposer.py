# -*- coding: utf-8 -*-
"""
提案生成（proposer.py）
======================
把 M4 的"问题簇"变成"可执行的提案"。

流程：
    ① 取最近一次聚类里 P0/P1 的簇
    ② 查这个簇的**主导 topic**（簇本身没存 topic，从它的成员反查）
    ③ **代码**按 topic 选工具 + 给处理方向（业务判断，不让 LLM 决定）
    ④ LLM 只写两样东西：回复/描述正文、一句理由
    ⑤ 写 action_proposal（args / evidence_refs / rationale 各归各位）

**为什么"选工具"不让 LLM 做**：
    物流慢 → 找物流方、客服差 → 找主管，这是**业务规则**，不是语言问题。
    让 LLM 选，它可能给"物流慢"配"改价"——看起来很合理，实际荒谬。
    这和"参数由代码算"是同一条原则（决策 #11）。

**LLM 只写正文和理由**：
    正文是话说得对不对的问题，理由是"为什么这么建议"，这两件才是它擅长的。
"""
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from hub.action.compliance import check as check_compliance
from hub.action.compliance import flag_strings
from hub.llm import TextLLM, get_text_llm
from hub.persistence.db import get_session
from hub.persistence.models import (
    AnalysisRun,
    ClusterMember,
    IssueCluster,
    ReviewTopic,
    SpuCatalog,
)
from hub.persistence.repo_cluster import ClusterRepo

# topic → 选哪个工具（业务规则，代码定）
#
# 为什么 quality 走"回复差评"而不是"改描述"：
#   面料扎人 / 开线 / 起球 是**真质量问题**，改详情页描述是粉饰，不解决问题。
#   运营真正会做的是：回复差评管理预期 + 反馈工厂。
#   真正适合改描述的是「描述本身缺信息」：尺码表没写、色差没提示。
TOOL_BY_TOPIC = {
    "quality": "review.draft_reply",        # 质量问题：回复 + 反馈工厂
    "size": "listing.update_desc",          # 尺码问题：补尺码表
    "desc_mismatch": "listing.update_desc",  # 描述不符：补色差/面料提示
    "logistics": "review.draft_reply",
    "service": "review.draft_reply",
    "misship": "review.draft_reply",
    "other": "review.draft_reply",
}

# topic → 回复里说的"我们会怎么处理"（**流程性表述，泛化没问题**）
# 注意：这里只能放"我们会做什么"，**不能放"商品是什么"**
# （比如"本款偏小"是事实断言，偏大就错了——那种必须从原文提取）
REPLY_REMEDY = {
    "quality": "会反馈给工厂核实工艺问题",
    "size": "会核对尺码表，把偏差标注出来",
    "desc_mismatch": "会调整详情页的图片和描述",
    "logistics": "会联系物流方加急跟进",
    "service": "会反馈给客服主管核实",
    "misship": "会核对发货记录，避免再出错",
    "other": "会记录并转给相关同事跟进",
}

MAX_REVIEWS_PER_DRAFT = 20      # 一次回复最多覆盖多少条评价
MAX_PROPOSALS = 6               # 一次最多出几个提案（运营看不过来更多）

REPLY_PROMPT = """我们要回复的是**同一类差评**（共 {n} 条），主要问题是：{label}

下面是其中几条原文：
{listing}

我们会怎么处理：{remedy}

请写一条**能覆盖这一类问题**的回复（40~80 个字）：
- 像人说话，别用"非常抱歉给您带来不愉快的购物体验"这类套话开头
- 说出这一类问题（用买家们的说法），再说我们会怎么处理
- **不要承诺退款、换货、补发、赔偿，也不要承诺时间**
- 话要实在，别空喊"会改进"

回复："""

DESC_HINT_PROMPT = """下面是同一个商品的一类差评（共 {n} 条），问题分类：{label}

差评原文：
{listing}

请用**一句客观陈述**（不超过 30 字）告诉**买家**：这个商品在「{label}」这一点上的实际情况是什么。

好例子（能直接贴到详情页给买家看）：
- 本款肩宽偏窄，宽肩建议选大一码
- 实物颜色比图片偏玫红，介意慎拍
- 面料偏硬，首次穿着建议内搭

坏例子（这是写给商家看的内部诊断，不是商品描述）：
- 版型描述中未说明肩宽问题
- 需要补充面料成分说明

要求：
- **只根据上面的原文**，不要编造
- 如实说偏差，**不要写成卖点**（买家说"肩宽对不上"，就写"肩宽偏窄"，不许写"肩部宽松适肩"）
- 只输出那一句话，不要解释

实际情况："""

RATIONALE_PROMPT = """运营系统发现了一个问题簇，准备采取一个动作。

问题：{label}（{count} 条差评，影响 {n_spu} 个商品）
动作：{tool_desc}

请用**一句中文**（不超过 40 字）说明为什么建议现在做这个动作。
要求：
- **必须带上「{label}」这个问题名**
- 只说业务理由，不要编造数字，不要空话（"提升体验""解决核心问题"这种不许写）

理由："""


@dataclass
class ProposalStats:
    """一次提案生成的统计。"""
    clusters: int = 0               # 参与生成的簇数
    proposals: int = 0              # 写了几条提案
    ids: list[str] = field(default_factory=list)       # 写进去的提案 ID（顺序即生成顺序）
    skipped: list[str] = field(default_factory=list)   # 跳过的簇（原因）
    elapsed_s: float = 0.0


def _dominant_topic(cluster_id, members: list) -> str:
    """簇的主导 topic。

    `issue_cluster` 没存 topic（聚类是按 topic 分组的，但没落库），
    所以从成员的 `review_topic.topic` 反查众数。
    """
    review_ids = [m.record_id for m in members]
    if not review_ids:
        return "other"
    with get_session() as s:
        rows = s.scalars(
            select(ReviewTopic.topic).where(ReviewTopic.record_id.in_(review_ids))
        ).all()
    if not rows:
        return "other"
    return Counter(rows).most_common(1)[0][0]


def _member_texts(members: list, limit: int) -> list[str]:
    """取簇成员的评论文本（给 LLM 当素材）。"""
    from hub.persistence.models import Record as RecordRow

    ids = [m.record_id for m in members[:limit]]
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.id, RecordRow.content_clean).where(RecordRow.id.in_(ids))
        ).all()
    m = {str(r.id): r.content_clean for r in rows}
    return [m[str(mid)] for mid in ids if str(mid) in m]


def _primary_spu(members: list, cluster) -> str | None:
    """簇里**出现最多**的商品 ID（不是随便取第一个）。

    为什么：一个簇可能覆盖几十个商品，取第一个会导致不同簇指向同一个商品
    （实测：两个簇都取了 JD-0001，生成的提案完全相同）。
    """
    from hub.persistence.models import Record as RecordRow

    ids = [m.record_id for m in members]
    if not ids:
        return (cluster.spu_ids or [None])[0]
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_spu_id).where(RecordRow.id.in_(ids))
        ).all()
    if not rows:
        return (cluster.spu_ids or [None])[0]
    return Counter(r[0] for r in rows).most_common(1)[0][0]


def _build_args(tool: str, cluster, members: list, topic: str, llm: TextLLM,
                platform: str = "taobao") -> dict:
    """按工具类型构造参数（**代码构造，不让 LLM 填**）。"""
    if tool == "review.draft_reply":
        remedy = REPLY_REMEDY.get(topic, REPLY_REMEDY["other"])
        texts = _member_texts(members, MAX_REVIEWS_PER_DRAFT)
        # 给 LLM 看 3 条原文就够了：太多会稀释"这一类问题是什么"
        listing = "\n".join(f"- {t[:80]}" for t in texts[:3]) or "- （无原文）"
        content = llm.chat(REPLY_PROMPT.format(
            n=cluster.review_count, label=cluster.label,
            listing=listing, remedy=remedy,
        ), max_tokens=200).strip()
        hits = check_compliance(content)
        return {
            "cluster_id": str(cluster.id),
            "review_ids": [str(m.record_id) for m in members[:MAX_REVIEWS_PER_DRAFT]],
            "content": content,
            "compliance_flags": flag_strings(hits),
        }

    # listing.update_desc
    spu_id = _primary_spu(members, cluster)
    if not spu_id:
        raise ValueError("簇没有关联商品，没法改描述")
    with get_session() as s:
        row = s.scalars(select(SpuCatalog)
                        .where(SpuCatalog.canonical_spu_id == spu_id)).first()
        old = (row.description if row and row.description else "") or \
              (row.title if row else "")
        if row and row.platform:
            platform = row.platform

    # 要补充什么信息，**从评论原文提取**（不能硬编码"本款偏小"——万一实际是偏大呢）
    texts = _member_texts(members, MAX_REVIEWS_PER_DRAFT)
    listing = "\n".join(f"- {t[:80]}" for t in texts[:3]) or "- （无原文）"
    hint = llm.chat(DESC_HINT_PROMPT.format(
        n=cluster.review_count, label=cluster.label, listing=listing,
    ), max_tokens=60).strip().splitlines()[0][:60]

    # **代码拼接，不再让 LLM 二次改写**。
    # 实测教训：让 LLM"改写整段描述"，它会把"肩宽对不上"写成"肩部宽松适肩"——
    # 投诉被美化成了卖点。事实只允许"提取+原样拼接"，不允许"润色"。
    new_desc = f"{old.strip()[:200]}【补充说明】{hint}"
    return {
        "platform": platform,
        "spu_id": spu_id,
        "description": new_desc[:500],
    }


def propose(max_proposals: int = MAX_PROPOSALS, llm: TextLLM | None = None) -> ProposalStats:
    """给 P0/P1 的簇生成提案（写库，不执行）。"""
    import time
    t0 = time.time()
    llm = llm or get_text_llm()
    stats = ProposalStats()

    clusters = [c for c in ClusterRepo().fetch_latest_clusters()
                if c.priority_tier in ("P0", "P1")]
    stats.clusters = len(clusters)
    if not clusters:
        print("没有 P0/P1 的簇（先跑 hub cluster + hub priority）")
        return stats

    # 建一次 analysis_run（提案要挂 run_id）
    from uuid import uuid4
    with get_session() as s:
        run = AnalysisRun(trace_id=f"propose-{uuid4().hex[:10]}",
                          tenant_id="default", status="RUNNING")
        s.add(run)
        s.flush()
        run_id = run.id
        trace_id = run.trace_id
        s.commit()

    from hub.persistence.models import ActionProposal
    from hub.persistence.models import SkuMapping
    from hub.tools.spec import get as get_spec

    seen: set[tuple] = set()     # (工具, 目标) 去重：同一个东西只出一个提案

    for cluster in clusters[:max_proposals]:
        with get_session() as s:
            # 代表评价（距质心最近）排前面：它们是"最能代表这个簇"的评价。
            # 不排序的话 members[0] 是随机的，可能出现"簇叫掉色、回复却说线头"。
            members = s.scalars(
                select(ClusterMember)
                .where(ClusterMember.cluster_id == cluster.id)
                .order_by(ClusterMember.is_representative.desc())
                .limit(MAX_REVIEWS_PER_DRAFT * 2)
            ).all()
        if not members:
            stats.skipped.append(f"{cluster.label}（没有成员）")
            continue

        topic = _dominant_topic(cluster.id, members)
        tool_name = TOOL_BY_TOPIC.get(topic, "review.draft_reply")
        spec = get_spec(tool_name)
        if spec is None:
            stats.skipped.append(f"{cluster.label}（工具未注册：{tool_name}）")
            continue

        # 去重：不同簇可能指向同一个商品（取"出现最多的商品"后更容易撞），
        # 同一个 (工具, 目标) 只出一个提案——否则运营会看到两条一模一样的待办。
        target = (str(cluster.id) if tool_name == "review.draft_reply"
                  else _primary_spu(list(members), cluster))
        sig = (tool_name, target)
        if sig in seen:
            stats.skipped.append(f"{cluster.label}（和前面提案目标相同：{target}）")
            continue
        seen.add(sig)

        try:
            args = _build_args(tool_name, cluster, list(members), topic, llm)
        except Exception as e:                  # noqa: BLE001
            stats.skipped.append(f"{cluster.label}（构造参数失败：{e}）")
            continue

        # 依据：这个簇本身
        evidence = {"issue_cluster": str(cluster.id)}

        # 理由 = 「问题名」+ LLM 写的一句判断
        #   问题名是**事实**（代码拼，保证一定出现），判断是 LLM 的活。
        #   只靠提示词要求"必须带问题名"，模型有时会漏（实测 4 条漏了 2 条）。
        try:
            llm_reason = llm.chat(RATIONALE_PROMPT.format(
                label=cluster.label,
                count=cluster.review_count,
                n_spu=len(cluster.spu_ids or []),
                tool_desc=spec.description,
            ), max_tokens=60).strip().splitlines()[0][:80]
        except Exception:                       # noqa: BLE001
            llm_reason = f"{cluster.review_count} 条差评，需尽快处理"
        rationale = f"「{cluster.label}」{llm_reason}"

        with get_session() as s:
            p = ActionProposal(
                run_id=run_id,
                trace_id=trace_id,
                tool_name=tool_name,
                tool_version=spec.version,
                args=args,
                risk_level=spec.risk_level,
                evidence_refs=[evidence],       # 只放依据
                rationale=rationale,            # 理由单独一列
                dry_run=True,
                status="PROPOSED",
            )
            s.add(p)
            s.flush()
            pid = str(p.id)
            s.commit()

        stats.proposals += 1
        stats.ids.append(pid)
        print(f"  提案 {stats.proposals}: [{cluster.priority_tier}] {cluster.label} "
              f"（{topic}）→ {tool_name} [{spec.risk_level}]")
        print(f"      理由：{rationale}")
        print(f"      args：{ {k: (v[:40] + '…' if isinstance(v, str) and len(v) > 40 else v) for k, v in args.items() if k != 'review_ids'} }")

    stats.elapsed_s = round(time.time() - t0, 2)
    return stats
