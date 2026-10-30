# -*- coding: utf-8 -*-
"""
业务数据现状（status.py）—— `hub status`
=========================================
回答：**"现在库里是什么情况"**。给运营 / 复盘看。

和 `health.py` 的分工（这是 M8 最重要的一条界限）：

    status  描述【数据现在什么样】  不随"跑一次"变，只看当前库
    health  描述【最近一次跑得怎么样】 每次跑完变

**为什么必须分开**：
    "降噪率涨了"（数据变了）和"兜底率涨了"（模型变差了）是**性质完全不同**的信号。
    混在一张表里，运营会读错。

指标分三块：
    ① 规模与影响  —— 有多少东西、值多少钱
    ② 处理漏斗    —— 发现了多少、做了多少、卡在谁手里
    ③ 数据质量    —— 这份数据能不能信
"""
from decimal import Decimal

from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import (
    ActionProposal,
    AnalysisRun,
    Anomaly,
    DeadLetter,
    HitlTask,
    IssueCluster,
    Record as RecordRow,
    ReviewTopic,
    SalesDaily,
    SpuCatalog,
)


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def _money(v: Decimal | float | None) -> str:
    """金额显示。

    ⚠️ 用全角 `￥`（U+FFE5）而不是半角 `¥`（U+00A5）：
        **半角那个不在 GBK 里**。中文 Windows 上，一旦输出被重定向/接管道
        （`| Select-String`、写日志、CI 抓输出），Python 会用 locale 默认编码
        （GBK）→ 遇到 `¥` 直接 UnicodeEncodeError 崩溃。
        实测踩过：`hub status | Select-String ...` 直接 traceback，
        也就是说**这个 CLI 在脚本/CI 里根本不能用**。
        全角 `￥` 在 GBK 里有编码，两边都不崩。
    """
    if not v:
        return "￥0"
    v = float(v)
    return f"￥{v / 10000:.1f} 万" if v >= 10000 else f"￥{v:.0f}"


def collect() -> list[tuple[str, list[tuple[str, str]]]]:
    """收集三块指标，返回 [(块名, [(指标名, 值), ...]), ...]。"""
    with get_session() as s:
        # ---------- 规模 ----------
        n_spu = s.scalar(select(func.count()).select_from(SpuCatalog)) or 0
        n_record = s.scalar(select(func.count()).select_from(RecordRow)) or 0
        n_keep = s.scalar(select(func.count()).select_from(RecordRow)
                          .where(RecordRow.dqe_decision == "keep")) or 0
        n_topic = s.scalar(select(func.count()).select_from(ReviewTopic)) or 0
        # "应分类" = 被保留 + 是差评（rating<=2）——分母不是全库，否则覆盖率永远很低
        n_should = s.scalar(select(func.count()).select_from(RecordRow)
                            .where(RecordRow.dqe_decision == "keep")
                            .where(RecordRow.rating.is_not(None))
                            .where(RecordRow.rating <= 2)) or 0

        keep_by_plat = dict(s.execute(
            select(RecordRow.platform, func.count())
            .where(RecordRow.dqe_decision == "keep")
            .group_by(RecordRow.platform)).all())
        all_by_plat = dict(s.execute(
            select(RecordRow.platform, func.count())
            .group_by(RecordRow.platform)).all())
        topic_by_plat = dict(s.execute(
            select(RecordRow.platform, func.count())
            .join(ReviewTopic, ReviewTopic.record_id == RecordRow.id)
            .group_by(RecordRow.platform)).all())

        # ---------- 漏斗 ----------
        #
        # ⚠️ 口径：**簇 / 异常 / 提案都只看【最近一次运行】**
        #
        # 为什么不能查全表：这三张表是"每次运行新建一批"（按 run_id 隔离），
        # 为了以后算环比。所以 `select(IssueCluster)` 全表查会把同一个问题数两遍
        # （实测：跑第二次后，"13 个簇"变成 26 个）。
        #
        # 而"商品/评价/差评"是 upsert 的，全表查才是对的——它们是存量。
        from hub.persistence.repo_cluster import ClusterRepo
        clusters = ClusterRepo().fetch_latest_clusters()

        # 只认"真正的提案运行"（带 stats.module=M5 标记）。
        # 不认的话，hub eval 为满足外键造的那条假提案会被当成最近一次运行。
        latest_proposal_run = s.scalar(
            select(ActionProposal.run_id)
            .join(AnalysisRun, AnalysisRun.id == ActionProposal.run_id)
            .where(AnalysisRun.stats["module"].astext == "M5")
            .order_by(ActionProposal.created_at.desc()).limit(1))
        proposals = s.scalars(
            select(ActionProposal)
            .where(ActionProposal.run_id == latest_proposal_run)).all() \
            if latest_proposal_run else []
        proposal_by_risk: dict = {}
        proposal_by_status: dict = {}
        for p in proposals:
            proposal_by_risk[p.risk_level] = proposal_by_risk.get(p.risk_level, 0) + 1
            proposal_by_status[p.status] = proposal_by_status.get(p.status, 0) + 1
        n_proposal = len(proposals)

        pids = [p.id for p in proposals]
        tasks = s.scalars(select(HitlTask).where(HitlTask.proposal_id.in_(pids))).all() \
            if pids else []
        task_by_status: dict = {}
        decided = []
        for t in tasks:
            task_by_status[t.status] = task_by_status.get(t.status, 0) + 1
            if t.decided_at:
                decided.append((t.decided_at, t.created_at))
        # 提案创建时间（算响应时长用）
        created_by_pid = {p.id: p.created_at for p in proposals}
        decided = [(t.decided_at, created_by_pid.get(t.proposal_id)) for t in tasks
                   if t.decided_at and created_by_pid.get(t.proposal_id)]

        n_dead = s.scalar(select(func.count()).select_from(DeadLetter)) or 0
        from hub.persistence.models import ActionOutcome
        n_waiting = s.scalar(select(func.count()).select_from(ActionOutcome)
                             .where(ActionOutcome.status == "WAITING")) or 0

        latest_anomaly_run = s.scalar(
            select(Anomaly.run_id).where(Anomaly.type == "price")
            .order_by(Anomaly.detected_at.desc()).limit(1))
        n_anomaly = s.scalar(
            select(func.count()).select_from(Anomaly)
            .where(Anomaly.type == "price")
            .where(Anomaly.run_id == latest_anomaly_run)) if latest_anomaly_run else 0

        # ---------- 影响面：GMV ----------
        # ⚠️ 注意：`sales_daily.canonical_spu_id` 里存的是【平台商品 ID】（TB-0006），
        #    不是对齐后的 canonical（SPU-0006）。所以**直接按平台 ID 查，不要经 sku_mapping 转**。
        #    这是"字段名叫 canonical、实际装平台 ID"的历史债（和 spu_catalog 同一个问题），
        #    记在推迟清单里（接真实数据前统一 canonical 化）。
        gmv_by_spu = dict(s.execute(
            select(SalesDaily.canonical_spu_id, func.sum(SalesDaily.gmv))
            .group_by(SalesDaily.canonical_spu_id)).all())
        # GMV 的窗口要从数据里算，不能写死"90 天"（mock 实际只有 30 天）
        gmv_win = s.execute(
            select(func.min(SalesDaily.stat_date), func.max(SalesDaily.stat_date),
                   func.count(func.distinct(SalesDaily.stat_date)))).first()

        # ---------- 质量 ----------
        n_fallback = s.scalar(select(func.count()).select_from(ReviewTopic)
                              .where(ReviewTopic.impl != "llm.v1")) or 0
        n_multi = s.scalar(select(func.count()).select_from(ReviewTopic)
                           .where(func.coalesce(func.array_length(
                               ReviewTopic.secondary_topics, 1), 0) > 0)) or 0

    # ==================== ① 规模与影响 ====================

    # 每个簇涉及多少 GMV（跨簇会重复计，所以这里报"Top3"而不是"总和"）
    cluster_gmv = []
    for c in clusters:
        g = sum(float(gmv_by_spu.get(spu) or 0) for spu in (c.spu_ids or []))
        cluster_gmv.append((c.label, c.priority_tier or "-", len(c.spu_ids or []), g))
    cluster_gmv.sort(key=lambda x: -x[3])
    gmv_top = cluster_gmv[:3]

    both = sum(1 for c in clusters if len(c.platforms or []) >= 2)

    scale = [
        ("商品（SPU）", f"{n_spu} 个"),
        ("评价总数", f"{n_record} 条（保留 {n_keep}）"),
        ("差评（已分类）", f"{n_topic} 条"),
        ("差评率",
         "｜".join(f"{p} {_pct(topic_by_plat.get(p, 0), keep_by_plat.get(p, 0))}"
                  for p in ("taobao", "jd"))),
        ("跨平台簇占比", f"{both}/{len(clusters)}（同时覆盖两平台）"),
        ("价格异常", f"{n_anomaly} 个"),
        # ⚠️ 叫"涉及面"而不是"影响"：这里算的是"该簇涉及的商品的 90 天 GMV"，
        #    同一个商品出现在多个簇里会被【重复计入】。
        #    用它排"先做哪个"够用（衡量曝光面），但**不能当成"损失金额"**——
        #    真正的损失要用因果估计，那是 M9 的 DID 干的事。
        ("GMV 涉及面 Top3",
         "｜".join(f"{lbl}({tier}) {_money(g)}" for lbl, tier, _, g in gmv_top) or "—"),
        ("GMV 口径", (f"{gmv_win[0]}~{gmv_win[1]}（{gmv_win[2]} 天）"
                      if gmv_win and gmv_win[0] else "无销售数据")),
    ]

    # ==================== ② 处理漏斗 ====================

    p0p1 = [c for c in clusters if c.priority_tier in ("P0", "P1")]
    # 一个 P0/P1 簇算"有去向"的条件：有提案行（PRPOSED/EXECUTED/SKIPPED 都算）。
    # ⚠️ 不能只数"提案条数"：一个簇可能出 0 条（漏了）或多条（不会）。
    #    更要紧的是**关系要靠 evidence_refs 判断**，不能从 args 里猜
    #    （listing.update_desc 的 args 是 spu_id，没有 cluster_id）。
    covered_clusters: set[str] = set()
    for p in s.scalars(select(ActionProposal)).all():
        for ref in (p.evidence_refs or []):
            cid = ref.get("issue_cluster") if isinstance(ref, dict) else None
            if cid:
                covered_clusters.add(str(cid))
    backlog = sum(1 for c in p0p1 if str(c.id) not in covered_clusters)

    pending = task_by_status.get("PENDING", 0)
    approved = task_by_status.get("APPROVED", 0)
    rejected = task_by_status.get("REJECTED", 0)
    decided_n = approved + rejected

    avg_hours = None
    if decided:
        spans = [(d - c).total_seconds() / 3600 for d, c in decided if d and c]
        avg_hours = sum(spans) / len(spans) if spans else None

    auto = sum(v for k, v in proposal_by_status.items() if k == "EXECUTED")

    funnel = [
        ("问题簇", f"{len(clusters)} 个（P0 {sum(1 for c in clusters if c.priority_tier == 'P0')}"
                   f"｜P1 {sum(1 for c in clusters if c.priority_tier == 'P1')}）"),
        ("生成提案", f"{n_proposal} 个"),
        ("待批", f"{pending} 个"),
        ("已执行", f"{auto} 个"),
        # "积压"这个词要慎用。系统里只允许两种"等"：
        #   等人批（有主）、等窗口到期（有主）。
        # "P0/P1 没有任何去向"是第三类：**没有主** —— 那是异常，不是常态。
        # 现在口径是：有提案行（含 SKIPPED）就算"有去向"，
        # 所以这里非 0 = 提案器漏了整个簇，是要修的。
        ("**P0/P1 无去向**", ("✅ 无（每个都有提案或跳过记录）" if backlog == 0
                            else f"⚠️ {backlog} 个（异常：提案器漏掉了整个簇）")),
        ("等人批", f"{pending} 个（有主：运营）"),
        ("批准率", f"{_pct(approved, decided_n)}（批 {approved}／拒 {rejected}）"
                   if decided_n else "还没有人批过"),
        ("响应时长", f"平均 {avg_hours:.1f} 小时（提案→批准）" if avg_hours else "—"),
        ("自动执行率", _pct(auto, n_proposal)),
        ("L2/L3 占比", _pct(proposal_by_risk.get("L2", 0) + proposal_by_risk.get("L3", 0),
                            n_proposal)),
        ("**等待观察窗口**", f"{n_waiting} 条（动作已做，14 天后才看得出效果）"),
        # 不用"积压"：死信是【有主】的（等人工核对），不是没人管。
        # 用"待核对"而不是"积压"，是在提示"这是别人的活儿，不是系统漏了"。
        ("死信待核对", f"{n_dead} 条（有主：人工）"),
        ("口径", "簇/异常/提案只看【最近一次运行】；商品/评价是存量"),
    ]

    # ==================== ③ 数据质量 ====================

    noise = "｜".join(
        f"{p} {_pct(all_by_plat.get(p, 0) - keep_by_plat.get(p, 0), all_by_plat.get(p, 0))}"
        for p in ("taobao", "jd"))

    quality = [
        ("降噪率（丢弃+待审）", noise),
        ("分类覆盖率", f"{n_topic}/{n_should} = {_pct(n_topic, n_should)}（差评维度）"),
        ("兜底率（非 LLM 分类）", _pct(n_fallback, n_topic)),
        ("多标签识别率", _pct(n_multi, n_topic)),
        ("分析覆盖率", f"{n_topic}/{n_record} = {_pct(n_topic, n_record)}（只分析差评）"),
    ]

    return [("规模与影响", scale), ("处理漏斗", funnel), ("数据质量", quality)]
