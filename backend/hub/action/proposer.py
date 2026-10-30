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
from hub.action.experience import advise
from hub.core.tracing import traced
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
MAX_PROPOSALS = 6               # **P2/P3** 一次最多出几个（P0/P1 不受此限，见 propose()）

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


def _apply_advice(topic: str, default_tool: str, adv) -> tuple[str, str | None, str]:
    """把经验建议应用到"选哪个工具 / 要不要降级"上。**纯函数，可单测。**

    :return: (最终工具, 要升到的风险等级或 None, 给运营看的说明)

    为什么单独抽出来：
        在真实数据上它**永远返回 (default_tool, None)**（样本不够门槛），
        所以"接线有没有接错"在真实数据上根本看不出来。
        而接错的后果很具体：库里写着 L2、实际却自动执行了
        —— **看起来降级了，其实没降**，是最危险的一种"假安全"。
        所以必须能用构造数据测（见 tests/check_advice_wiring.py）。
    """
    if adv is None:
        return default_tool, None, ""
    tool = adv.prefer_tool or default_tool
    floor = "L2" if adv.escalate else None
    note = adv.reason if adv.has_advice else ""
    return tool, floor, note


def _schema_error(spec, args: dict) -> str:
    """按工具的 args_schema 校验参数。返回错误信息，合法则返回空串。

    在**提案这一层**校验，而不是等网关拒：
        网关必须校验（它是最后一道闸），但那时提案已经进库了，
        留下一条注定被拒的记录。这里先拦住，把问题变成 SKIPPED + 原因。
    """
    try:
        schema = getattr(spec, "args_schema", None)
        if not schema:
            return ""
        from jsonschema import validate       # 项目里 pydantic 已带 jsonschema 依赖
        validate(instance=args, schema=schema)
        return ""
    except Exception as e:                    # noqa: BLE001
        return str(e).splitlines()[0][:80]


def _record_skipped(cluster, run_id, trace_id: str, reason: str) -> None:
    """把"这个簇被跳过了"落盘成一条 status=SKIPPED 的提案记录。

    **为什么"不做的决定"也要落盘**：
        系统里最糟的状态不是"没做"，是"不知道为什么没做"。
        只打印一句日志的话，下游（hub today / status）只能看到
        "这个 P0 没有提案"，却分不清是被上限挡住、工具没注册、还是去重撞了。

        落盘之后，"每个高优簇都有明确的去向"：
            status=PROPOSED  → 有提案（可能在等人批）
            status=SKIPPED   → 有结论：不做，原因是 X
            什么都没有       → **这才是异常**

    注意：`evidence_refs` 里写 `issue_cluster`，这样下游不用猜
        （`listing.update_desc` 的 args 是 spu_id，从 args 反推不出簇）。
    """
    from hub.persistence.models import ActionProposal as _AP
    with get_session() as s:
        s.add(_AP(
            run_id=run_id, trace_id=trace_id,
            tool_name="(skipped)", tool_version="-",
            args={}, risk_level="L0",
            evidence_refs=[{"issue_cluster": str(cluster.id)}],
            rationale=f"跳过「{cluster.label}」：{reason}",
            dry_run=True, status="SKIPPED",
        ))
        s.commit()


@traced("m5.propose")
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
    #
    # `stats={"module": "M5"}` 是**必须的标记**：
    #   报表（hub status）要能区分"真正的提案运行"和"测试/工具造的临时 run"。
    #   实测踩过：hub eval 为了让 tool_execution 的外键成立，造了一条假提案，
    #   结果 hub status 把它当成"最近一次提案运行"，显示"生成提案 1 个"。
    #   标记 module 之后，报表只认带标记的 run。
    from uuid import uuid4
    with get_session() as s:
        run = AnalysisRun(trace_id=f"propose-{uuid4().hex[:10]}",
                          tenant_id="default", status="RUNNING",
                          stats={"module": "M5"})
        s.add(run)
        s.flush()
        run_id = run.id
        trace_id = run.trace_id
        s.commit()

    from hub.persistence.models import ActionProposal
    from hub.persistence.models import SkuMapping
    from hub.tools.spec import get as get_spec

    seen: set[tuple] = set()     # (工具, 目标) 去重：同一个东西只出一个提案

    # 选哪些簇出提案：**P0/P1 全出，P2/P3 最多 max_proposals 个**。
    #
    # 为什么改成这样（原来是 `clusters[:max_proposals]`）：
    #   原来的上限是"总共最多 6 个"，于是 P0/P1 有 7 个时，
    #   剩下的 1 个【没人管】——那才是真正的"积压"。
    #   `hub status` 里那行"积压 1 个 P0/P1 还没出提案"就是它。
    #
    #   原则：系统里只允许两种"等"——等人批（有主）、等窗口到期（有主）。
    #   "P0/P1 没出提案"是第三类：**没有主**。这类状态不该存在。
    #
    #   上限仍然保留，但它只约束【低优先级】：P2/P3 本来就是"可以晚点做"的，
    #   而 P0/P1 晚一天就是一天的钱。
    urgent = [c for c in clusters if (c.priority_tier or "") in ("P0", "P1")]
    rest = [c for c in clusters if (c.priority_tier or "") not in ("P0", "P1")]
    selected = urgent + rest[:max_proposals]

    for cluster in selected:
        # 被跳过的簇也要**落盘一条记录**（status=SKIPPED + 原因）。
        #
        # 为什么：系统里最糟的状态不是"没做"，是"**不知道为什么没做**"。
        #   原来的实现只是 `stats.skipped.append(...)` —— 打印一次就没了，
        #   于是下游（hub today / status）只能看到"这个 P0 没有提案"，
        #   分不清是"被上限挡住了"、"工具没注册"、还是"去重撞了"。
        #   实测踩过：3 个 P0/P1 被报"没有提案"，其中 2 个其实有提案，
        #   只是提案的 args 用 spu_id、没有 cluster_id —— 关系丢了。
        #
        #   修法：① 关系用 evidence_refs 落盘（那里一直写着 issue_cluster）；
        #        ② 跳过的也写一行，带上原因。
        #   这样"每一个高优簇都有明确的去向"，不再有"没有主"的状态。
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
            _record_skipped(cluster, run_id, trace_id, "没有成员评价")
            stats.skipped.append(f"{cluster.label}（没有成员）")
            continue

        topic = _dominant_topic(cluster.id, members)
        default_tool = TOOL_BY_TOPIC.get(topic, "review.draft_reply")

        # ★ 动作经验回灌（M9 → M5 的闭环）
        #
        #   注意：**这里不改工具的 risk_level，也不改工具定义**。
        #   经验只影响两件事：选哪个工具（prefer_tool）、
        #   以及这一次要不要人工确认（escalate → 风险下限 L2）。
        adv = advise(topic, default_tool)
        tool_name, risk_floor, exp_note = _apply_advice(topic, default_tool, adv)

        spec = get_spec(tool_name)
        if spec is None:
            _record_skipped(cluster, run_id, trace_id,
                            f"工具未注册：{tool_name}")
            stats.skipped.append(f"{cluster.label}（工具未注册：{tool_name}）")
            continue

        # 去重：不同簇可能指向同一个商品（取"出现最多的商品"后更容易撞），
        # 同一个 (工具, 目标) 只出一个提案——否则运营会看到两条一模一样的待办。
        target = (str(cluster.id) if tool_name == "review.draft_reply"
                  else _primary_spu(list(members), cluster))
        sig = (tool_name, target)
        if sig in seen:
            _record_skipped(cluster, run_id, trace_id,
                            f"和前面的提案目标相同（{tool_name} → {target}）")
            stats.skipped.append(f"{cluster.label}（和前面提案目标相同：{target}）")
            continue
        seen.add(sig)

        try:
            args = _build_args(tool_name, cluster, list(members), topic, llm)
        except Exception as e:                  # noqa: BLE001
            _record_skipped(cluster, run_id, trace_id, f"构造参数失败：{e}")
            stats.skipped.append(f"{cluster.label}（构造参数失败：{e}）")
            continue

        # ★ 提交前先按工具 schema 校验一次
        #
        # 为什么：网关会校验（而且拦住了我们一次），但那时提案已经写进库了，
        # 留下一条"注定被拒"的记录。在提案这一层先校验，就能把问题变成
        # SKIPPED + 原因，而不是每条提案都去网关撞一次墙。
        # 实测踩过：往 args 里塞了 `topic`（schema 里没有）→ 7 个提案全被拒。
        bad = _schema_error(spec, args)
        if bad:
            _record_skipped(cluster, run_id, trace_id, f"参数不符合工具 schema：{bad}")
            stats.skipped.append(f"{cluster.label}（参数不合法：{bad}）")
            continue

        # ★ topic 不能放进 args：它不属于工具参数，属于提案的属性。
        #   实测踩过：塞进 args["topic"] 后 7 个提案全被网关拒（PARAM_INVALID），
        #   因为 args_schema 里没有这个字段。网关是对的，错的是我们。
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
        # 把经验判断的依据挂上去 —— 运营要能看到"系统为什么这么选/为什么降级"
        if exp_note:
            rationale = f"{rationale}（经验：{exp_note}）"

        with get_session() as s:
            p = ActionProposal(
                run_id=run_id,
                trace_id=trace_id,
                tool_name=tool_name,
                tool_version=spec.version,
                args=args,
                # 提案记录"实际生效的等级"：经验要求收紧时写 L2（只升不降）。
                # ⚠️ 关键：这个值必须被【传给网关】（cli.cmd_propose 里 risk_floor=p.risk_level），
                #    否则就是"库里写着 L2、实际却自动执行了"——看起来降级了，其实没降。
                risk_level=risk_floor or spec.risk_level,
                topic=topic,
                # 实际生效等级 = max(提案等级, 工具声明)，网关侧再取一次 max
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
              f"（{topic}）→ {tool_name} [{spec.risk_level}]"
              + ("  ⬆️ 经验降级为 L2" if risk_floor else ""))
        if exp_note:
            print(f"      经验：{exp_note}")
        print(f"      理由：{rationale}")
        print(f"      args：{ {k: (v[:40] + '…' if isinstance(v, str) and len(v) > 40 else v) for k, v in args.items() if k != 'review_ids'} }")

    stats.elapsed_s = round(time.time() - t0, 2)

    # 收尾：把这次提案运行标成 DONE（否则 analysis_run 里会一直挂 RUNNING）
    from hub.persistence.repository import RunRepo
    RunRepo().update_status(
        trace_id, "DONE",
        stats={"module": "M5",
               "summary": {"proposals": stats.proposals,
                           "skipped": len(stats.skipped)}},
    )
    return stats
