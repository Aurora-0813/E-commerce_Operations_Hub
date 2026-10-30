# -*- coding: utf-8 -*-
"""
命令行入口（cli.py）
===================
用法（在 backend/ 目录下）：
    python -m hub.cli skeleton     # 跑一次假数据全链（walking skeleton）
    python -m hub.cli p0 <文件> --platform jd --type review
                                   # 读一个导出文件，出 RawBatch + 记录 + 质量报告

为什么要有 CLI：
    开发期用命令行跑一次，比写临时脚本方便。以后会扩展成完整 hubctl。
"""
import argparse
from uuid import uuid4


def cmd_skeleton() -> None:
    """跑一次 walking skeleton：建批次 → 跑 8 步 → 打印结果 → 验证落库。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import AnalysisRun, IngestBatch, TraceSpan
    from hub.pipeline.runner import run_skeleton

    # 1. 建一个导入批次（analysis_run.batch_id 外键依赖它）
    batch_id = uuid4()
    with get_session() as s:
        s.add(IngestBatch(
            id=batch_id,
            tenant_id="default",
            platform="jd",
            file_name="skeleton_demo.xlsx",
            file_sha256=uuid4().hex * 2,   # 每次不同，避免唯一约束冲突
        ))
        s.commit()
    print(f"批次已建：{batch_id}")

    # 2. 跑流水线
    trace_id = f"trace_{uuid4().hex[:8]}"
    print(f"开始 walking skeleton，trace_id={trace_id}\n")
    state = run_skeleton(trace_id, batch_id)

    # 3. 打印每一步的产出
    for name, out in state.outputs.items():
        print(f"  {name:12s} -> {out}")

    # 4. 验证落库
    with get_session() as s:
        run = s.scalar(select(AnalysisRun).where(AnalysisRun.trace_id == trace_id))
        span_count = s.scalar(
            select(func.count()).select_from(TraceSpan).where(TraceSpan.trace_id == trace_id)
        )
    print(f"\n✅ 完成：trace_id={trace_id}")
    print(f"   落库验证：analysis_run.status={run.status}，trace_span 共 {span_count} 条")


def cmd_p0(file: str, platform: str, data_type: str, save: bool = True) -> None:
    """读一个平台导出文件 → 打印质量报告 → 落库（默认落库）。"""
    from hub.ingress.adapter import read_file
    from hub.persistence.repo_ingress import IngressRepo

    batch, records, report = read_file(file, platform, data_type)

    # ---- 批次信息 ----
    print("=" * 50)
    print(f"文件：{batch.file_name}")
    print(f"平台/类型：{batch.platform}/{batch.data_type}")
    print(f"SHA256：{batch.file_sha256[:16]}...")
    print(f"sheet：{len(batch.sheets)} 个")
    for sh in batch.sheets:
        print(f"  - {sh.name}：表头在第 {sh.header_row} 行，共 {sh.row_count} 行，坏行 {len(sh.bad_rows)}")

    # ---- 质量报告 ----
    print("\n--- 质量报告 ---")
    print(f"总行数 {report.total}｜成功 {report.row_ok}｜坏行 {report.row_bad}")
    print(f"PII 丢弃：{report.pii_stats}")
    print(f"字段覆盖率：{report.field_coverage}")
    print(f"未对齐同款比例：{report.unmapped_rate}")
    if report.warnings:
        print(f"警告：{report.warnings}")

    # ---- 样例记录 ----
    if records:
        r = records[0]
        print("\n--- 样例记录（第 1 条）---")
        print(f"platform_id={r.platform_id}  rating={r.rating}  ts={r.ts}  impl={r.impl}")
        print(f"fields={r.fields}")
        print(f"monetary={r.monetary}")

    # ---- 坏行样例 ----
    bads = [b for sh in batch.sheets for b in sh.bad_rows]
    if bads:
        print("\n--- 坏行样例（前 3 条）---")
        for b in bads[:3]:
            print(f"  第 {b.line_no} 行：{b.reason}")

    # ---- 落库 ----
    if save:
        repo = IngressRepo()
        batch_id, existed = repo.save(batch, records, report)
        if existed:
            print(f"\n⚠️  该文件已导入过，本次跳过（batch_id={batch_id}）")
        else:
            print(f"\n✅ 已入库：batch_id={batch_id}，共 {len(records)} 条记录")
    else:
        print("\n（--no-save：仅预览，未写库）")


def cmd_dqe(force: bool = False, batch: str | None = None) -> None:
    """跑数据降噪：读 record → 判定 → 回填 dqe_* 字段。"""
    from uuid import UUID

    from hub.dqe.engine import run_dqe

    batch_id = UUID(batch) if batch else None
    print(f"开始 DQE（{'全量重判' if force else '增量'}）……")
    stats = run_dqe(batch_id=batch_id, force=force)

    print(f"\n处理 {stats.total} 条，耗时 {stats.elapsed_s}s，引擎 {stats.engine}"
          f"{'（已降级）' if stats.degraded else ''}")
    print(f"  keep   {stats.keep}")
    print(f"  review {stats.review}")
    print(f"  drop   {stats.drop}")
    if stats.total:
        ratio = (stats.review + stats.drop) / stats.total
        print(f"  降噪率（review+drop）{ratio:.1%}")


def cmd_mm(package: str, force: bool = False, batch: str | None = None,
           limit: int | None = None, vision: str = "mock") -> None:
    """跑多模态处理：图片包 → 沙箱 → Vision → 覆写销毁 → review_feature。"""
    from uuid import UUID

    from hub.multimodal.engine import run_multimodal
    from hub.multimodal.vision import get_vision

    batch_id = UUID(batch) if batch else None
    v = get_vision(vision)
    print(f"开始多模态处理（图片包：{package}｜Vision：{v.name}）……")
    stats = run_multimodal(package, batch_id=batch_id, force=force, vision=v, limit=limit)

    print(f"\n评价：待处理 {stats.total} 条，成功 {stats.processed}，跳过 {stats.skipped}")
    print(f"图片：共 {stats.images} 张｜pHash 去重 {stats.deduped} 张｜"
          f"实际调 Vision {stats.images - stats.deduped} 次｜失败 {stats.failed} 张")
    print(f"  元数据剥离 {stats.stripped} 张（GPS/设备信息已清除）")
    print(f"  覆写销毁 {stats.destroyed} 个文件")
    print(f"  沙箱残留 {stats.residual} 个文件（应为 0）")
    print(f"  耗时 {stats.elapsed_s}s")
    print(f"  审计记录 trace_id = {stats.trace_id}（analysis_run，可查）")


def cmd_topic(force: bool = False, limit: int | None = None,
              batch_size: int = 10) -> None:
    """跑 M3 归因链：分类 → 聚合 → 归因。"""
    from hub.analysis.engine import run_topic_analysis

    print("开始 M3 归因链（分类 → 聚合 → 归因）……")
    stats = run_topic_analysis(force=force, limit=limit, batch_size=batch_size)

    print(f"\n分类：待处理 {stats.total} 条，写入 {stats.classified} 条")
    print(f"  批量成功 {stats.batch_ok}｜逐条救回 {stats.retry_ok}｜"
          f"兜底 {stats.fallback}｜批次数 {stats.batches}")
    print(f"  多标签（识别出 2 个问题）{stats.multi_label} 条")
    print(f"归因：格子 {stats.cells} 个，成功 {stats.attributed}，"
          f"样本不足跳过 {stats.skipped_cells}，失败 {stats.failed_cells}")
    print(f"  证据 {stats.evidence_total} 条，丢弃编造 {stats.evidence_dropped} 条")
    print(f"  耗时 {stats.elapsed_s}s｜trace_id = {stats.trace_id}")


def cmd_align() -> None:
    """跑跨平台同款对齐（M11）：读商品目录 → 对齐 → 写 sku_mapping。"""
    from hub.analysis.align import run_alignment

    print("开始同款对齐（TF-IDF + 余弦相似度）……")
    result = run_alignment()

    print(f"\n自动匹配 {result.matched_pairs} 对")
    print(f"  淘宝独占 {result.tb_only} 个｜京东独占 {result.jd_only} 个")
    print(f"  canonical 总数 {result.canonical_total} 个")
    print(f"  人工候选 {len(result.review_pairs)} 对（置信度介于 0.60~0.85）")
    print(f"  写入 sku_mapping {len(result.mappings)} 行")


def cmd_cluster(max_per_topic: int = 5) -> None:
    """跑 M4 分层聚类（按 topic 分组 → 组内 KMeans → LLM 命名）。"""
    from hub.analysis.cluster import run_clustering

    print("开始分层聚类（topic 分组 → TF-IDF + SVD + KMeans → LLM 命名）……")
    stats = run_clustering(max_per_topic=max_per_topic)

    print(f"\n差评 {stats.total_reviews} 条｜topic {stats.topics} 个"
          f"｜子簇 {stats.clusters} 个")
    print(f"  命名成功 {stats.named} 个｜写入 issue_cluster {stats.wrote} 行"
          f"｜cluster_member {stats.members} 行")
    if stats.sizes:
        print(f"  簇大小：{sorted(stats.sizes, reverse=True)}")
    print(f"  耗时 {stats.elapsed_s}s｜trace_id = {stats.trace_id}")


def cmd_price() -> None:
    """跑 M4 价格背离检测（需要先跑 hub align）。"""
    from hub.analysis.price import run_price_divergence

    print("开始价格背离检测（dev + 差评率分位）……")
    stats = run_price_divergence()

    print(f"\n有跨平台价格的商品 {stats.spus} 个")
    print(f"  检出异常 {stats.flagged} 个｜写入 anomaly {stats.wrote} 行")
    print(f"  耗时 {stats.elapsed_s}s｜trace_id = {stats.trace_id}")


def cmd_priority() -> None:
    """跑 M4 优先级打分（给最近一次聚类的每个簇打分）。"""
    from hub.analysis.priority import run_priority

    print("开始优先级打分（reach + severity + trend + actionability）……")
    stats = run_priority()

    print(f"\n簇 {stats.clusters} 个｜打分 {stats.scored} 个")
    if stats.tiers:
        print("  分档：" + "｜".join(f"{k} {v} 个" for k, v in sorted(stats.tiers.items())))
    if stats.top:
        print("  Top 3：")
        for label, score, tier, n, reach in stats.top:
            print(f"    {tier} {score:>5.1f}  {label}（{n} 条）")
    print(f"  耗时 {stats.elapsed_s}s")


def cmd_propose(max_proposals: int = 6, execute: bool = True) -> None:
    """M5：从 P0/P1 问题簇生成提案，并过一遍网关。"""
    from uuid import UUID

    from hub.action.proposer import propose
    from hub.gateway import Decision, ToolCall, invoke
    from hub.persistence.db import get_session
    from hub.persistence.models import ActionProposal
    from hub.tools.spec import CallContext

    print("开始生成提案（代码选工具 + LLM 写正文和理由）……")
    stats = propose(max_proposals=max_proposals)

    print(f"\n生成 {stats.proposals} 个提案（P0/P1 簇 {stats.clusters} 个）")
    for s in stats.skipped:
        print(f"  跳过：{s}")
    if not execute or not stats.ids:
        return

    print("\n=== 过网关 ===")
    # 用 manager 角色：L0/L1 会直接执行（设计如此），L2/L3 依然要人批
    from hub.core.tracing import current_trace_id
    ctx = CallContext(role="manager", trace_id=current_trace_id() or "propose-cli")
    tally = {}
    for pid in stats.ids:
        with get_session() as s:
            p = s.get(ActionProposal, UUID(pid))
            if p is None:
                continue
            call = ToolCall(
                tool_name=p.tool_name,
                args=p.args,
                evidence_refs={k: v for ref in (p.evidence_refs or []) for k, v in ref.items()},
                proposal_id=pid,
            )
            label = (p.rationale or "")[:30]
        r = invoke(call, ctx)
        tally[r.decision.value] = tally.get(r.decision.value, 0) + 1
        task = (r.detail or {}).get("hitl_task_id")
        extra = f"｜待办 {task[:8]}" if task else ""
        if r.deny_code:
            extra = f"｜{r.deny_code}"
        print(f"  {r.brief()}{extra}  ← {label}")

    print("\n汇总：" + "｜".join(f"{k} {v} 个" for k, v in sorted(tally.items())))
    print("说明：L0/L1 已自动执行并回查；L2/L3 只建了待办，**没有动任何数据**。")


def cmd_pending() -> None:
    """M5：看待审批的待办。"""
    from hub.action.approval import list_pending

    rows = list_pending()
    if not rows:
        print("没有待审批的提案")
        return
    print(f"待审批 {len(rows)} 条：\n")
    for r in rows:
        print(f"  [{r['risk']}] {r['tool']}")
        print(f"      理由：{r['rationale']}")
        print(f"      参数：{r['args_preview']}")
        print(f"      待办：{r['task_id']}（{r['expires_at']} 过期）")
        print()


def cmd_approve(task_id: str, by: str, note: str | None = None) -> None:
    """M5：批准并执行。"""
    from hub.action.approval import approve

    print(f"批准待办 {task_id}（{by}）……")
    res = approve(task_id, by, note)
    if res.get("ok"):
        print(f"✅ 已执行｜回查 {'通过' if res.get('verify_ok') else '未通过'}")
        if res.get("detail", {}).get("compensate"):
            print(f"  补偿：{res['detail']['compensate']}")
    else:
        print(f"❌ {res.get('reason') or res.get('deny_code')}")


def cmd_reject(task_id: str, by: str, note: str | None = None) -> None:
    """M5：拒绝（不执行）。"""
    from hub.action.approval import reject

    res = reject(task_id, by, note)
    if res.get("ok"):
        print(f"✅ 已拒绝（但什么都没做——拒绝就是不做）")
    else:
        print(f"❌ {res.get('reason')}")


def cmd_trace(trace_id: str | None = None) -> None:
    """M8：看一次运行的链路树（不传 trace_id 就列最近的）。"""
    from sqlalchemy import func

    from hub.core.tracing import print_tree
    from hub.persistence.db import get_session
    from hub.persistence.models import TraceSpan

    if not trace_id:
        with get_session() as s:
            rows = s.execute(
                select(TraceSpan.trace_id,
                       func.count(TraceSpan.span_id),
                       func.min(TraceSpan.start_ts),
                       func.max(TraceSpan.end_ts))
                .group_by(TraceSpan.trace_id)
                .order_by(func.max(TraceSpan.start_ts).desc())
                .limit(10)
            ).all()
        if not rows:
            print("还没有任何 trace（先跑 `python scripts/run_all.py`）")
            return
        print("最近的 trace（用 `hub trace <trace_id>` 看详情）：\n")
        for tid, n, st, et in rows:
            dur = (et - st).total_seconds() if st and et else 0
            print(f"  {tid[:16]}…  {n:4d} 个 span   {dur:8.1f}s   {st}")
        return

    print_tree(trace_id)


def _print_sections(title: str, sections: list[tuple[str, list[tuple[str, str]]]]) -> None:
    """统一的报表打印：大标题 + 分块 + 指标对齐。"""
    print(f"\n{'=' * 66}")
    print(f"  {title}")
    print(f"{'=' * 66}")
    for name, rows in sections:
        print(f"\n【{name}】")
        for label, value in rows:
            print(f"  {label:<22} {value}")
    print()


def cmd_status() -> None:
    """M8：业务数据现状（运营看）。"""
    from hub.observability.status import collect

    _print_sections("业务数据现状（hub status）", collect())
    print("说明：这里看的是「当前库里的状态」，不随「跑一次」变。")
    print("      想看「最近一次跑得怎么样」，用 `hub health`。")


def cmd_health(trace_id: str | None = None) -> None:
    """M8：最近一次运行的健康度（工程看）。"""
    from hub.observability.health import collect

    _print_sections("最近一次运行健康度（hub health）", collect(trace_id))
    print("说明：这里看的是「最近一次运行」的耗时/成本/降级，每次跑完会变。")
    print("      想看单次明细（哪一步慢），用 `hub trace <trace_id>`。")


def cmd_retro(dry_run: bool = False, all_: bool = False) -> None:
    """M9：效果复盘（DID）。

    **只在观察窗口走完之后才算**（默认）；`--all` 连没到期的也算，
    但那时后窗口还没数据，结论一定是 INCONCLUSIVE —— 只用来调试。
    """
    from hub.retro.report import print_report, run

    done, waiting = run(apply=not dry_run, only_due=not all_)
    print_report(done, waiting)
    if dry_run:
        print("\n（--dry-run：结论没有写回数据库）")


def cmd_eval(suite: str = "perturbation_v1", strict: bool = False) -> int:
    """M8：跑离线评估套件，返回退出码（0 通过 / 非 0 门禁不过）。"""
    from hub.eval.runner import run

    print(f"跑用例集 {suite} ……\n")
    report = run(suite)

    last_cat = None
    for r in report.results:
        if r.category != last_cat:
            print(f"\n【{r.category}】")
            last_cat = r.category
        icon = "✅" if r.passed else "❌"
        gate = "hard" if r.gate == "hard" else "soft"
        print(f"  {icon} {r.id:<3} [{gate}] {r.title}")
        if not r.passed:
            print(f"        期望 {r.expect}")
            print(f"        实际 {r.actual}" + (f"｜{r.error}" if r.error else ""))

    hard, soft = report.hard, report.soft
    hard_ok = sum(1 for r in hard if r.passed)
    soft_ok = sum(1 for r in soft if r.passed)

    print(f"\n{'=' * 66}")
    print(f"  硬门禁（安全类）{hard_ok}/{len(hard)} = {report.hard_rate * 100:.0f}%"
          f"   要求 100%")
    soft_req = "100%（--strict）" if strict else "≥95%"
    print(f"  软门禁（质量类）{soft_ok}/{len(soft)} = {report.soft_rate * 100:.0f}%"
          f"   要求 {soft_req}")
    code = report.exit_code
    if strict and code == 0 and report.soft_rate < 1.0:
        code = 3                              # --strict：软门禁也要求全过
    print(f"  结论：{'✅ 通过' if code == 0 else '❌ 不通过'}"
          f"（退出码 {code}）")
    print(f"{'=' * 66}")
    if code:
        print("\n提示：硬门禁失败必须修；软门禁允许极少数抖动。")
    return code


def main() -> None:
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(prog="hub", description="跨平台电商 AI 运营决策中枢 CLI")
    # 全局开关：跑完打印本次调用的链路树（放在子命令前面，如 `hub --trace mm ...`）
    parser.add_argument("--trace", action="store_true",
                        help="跑完打印本次调用的链路树（M8）")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("skeleton", help="跑一次假数据全链（walking skeleton）")

    p0 = sub.add_parser("p0", help="读一个导出文件，出报告并落库")
    p0.add_argument("file", help="文件路径（xlsx）")
    p0.add_argument("--platform", required=True, choices=["taobao", "jd"])
    p0.add_argument("--type", dest="data_type", required=True,
                    choices=["review", "product", "sales"])
    p0.add_argument("--no-save", action="store_true", help="只预览，不写库")

    dqe = sub.add_parser("dqe", help="跑数据降噪（M1 DQE）")
    dqe.add_argument("--force", action="store_true", help="重判全部（默认只判未判过的）")
    dqe.add_argument("--batch", default=None, help="只处理指定批次 UUID")

    mm = sub.add_parser("mm", help="跑多模态处理（M2 图片沙箱）")
    mm.add_argument("package", help="图片包路径（目录或 zip）")
    mm.add_argument("--force", action="store_true", help="重处理（覆盖已有 review_feature）")
    mm.add_argument("--batch", default=None, help="只处理指定批次 UUID")
    mm.add_argument("--limit", type=int, default=None, help="只处理前 N 条（本地模型慢，先抽检）")
    mm.add_argument("--vision", default="mock", choices=["mock", "ollama"],
                    help="Vision 实现：mock（快）或 ollama（本地 Qwen2.5-VL）")

    topic = sub.add_parser("topic", help="跑 M3 归因链（分类 + 归因）")
    topic.add_argument("--force", action="store_true",
                       help="全量重算（默认只处理还没分类的）")
    topic.add_argument("--limit", type=int, default=None, help="只处理前 N 条（抽检）")
    topic.add_argument("--batch-size", type=int, default=10, help="每批分类几条")

    sub.add_parser("align", help="跑跨平台同款对齐（M11，M4 的前置）")

    cluster = sub.add_parser("cluster", help="跑 M4 分层聚类（topic → 子簇）")
    cluster.add_argument("--max-per-topic", type=int, default=5,
                         help="单个 topic 最多拆几个子簇（默认 5）")

    sub.add_parser("price", help="跑 M4 价格背离检测（需先 hub align）")

    sub.add_parser("priority", help="跑 M4 优先级打分（需先 hub cluster）")

    propose_p = sub.add_parser("propose", help="M5：从 P0/P1 簇生成提案并过网关")
    propose_p.add_argument("--max", type=int, default=6, help="最多生成几个提案")
    propose_p.add_argument("--dry-run", action="store_true", help="只生成，不过网关")

    sub.add_parser("pending", help="M5：看待审批的待办")

    ap = sub.add_parser("approve", help="M5：批准一条待办并执行")
    ap.add_argument("task_id", help="待办 ID（hub pending 能看到）")
    ap.add_argument("--by", required=True, choices=["operator", "manager"],
                    help="批准人角色（L2 要 operator，L3 要 manager）")
    ap.add_argument("--note", default=None, help="审批备注")

    rj = sub.add_parser("reject", help="M5：拒绝一条待办（不执行）")
    rj.add_argument("task_id", help="待办 ID")
    rj.add_argument("--by", required=True, choices=["operator", "manager"])
    rj.add_argument("--note", default=None, help="拒绝理由")

    tr = sub.add_parser("trace", help="M8：看一次运行的链路树")
    tr.add_argument("trace_id", nargs="?", default=None,
                    help="trace ID（不传就列最近的 10 次）")

    sub.add_parser("status", help="M8：业务数据现状（运营看）")
    hp = sub.add_parser("health", help="M8：最近一次运行的健康度（工程看）")
    hp.add_argument("--id", dest="health_trace", default=None,
                    help="指定 trace_id（默认取最近一次全链）")

    # ---- M9 效果复盘 ----
    rp = sub.add_parser("retro", help="M9：效果复盘（DID，判动作有没有用）")
    rp.add_argument("--dry-run", action="store_true",
                    help="只算不写库（先看结论对不对）")
    rp.add_argument("--all", action="store_true",
                    help="连没到观察窗口的也算（默认只算到期的）")

    # ---- M7 调度 ----
    # 注意：**没有 `--kind` 参数**。现在只允许 kind="full"，
    # 因为 `full` 和 `backfill` 能同时入队（部分唯一索引是"同 kind 唯一"），
    # 而两条链会同时写 record/issue_cluster → 数据打架。
    # 等真要回填时，把索改成"全局只允许一条链"再加 kind。
    sub.add_parser("enqueue", help="M7：把一条全链塞进队列")

    wk = sub.add_parser("worker", help="M7：常驻消费队列")
    wk.add_argument("--once", action="store_true", help="只处理一条就退出（测试用）")
    wk.add_argument("--idle", type=int, default=None,
                    help="覆盖抢占阈值（秒）。有 PEL 心跳续租，调小是安全的"
                         "（只影响'真崩了多久被接管'）")

    qp = sub.add_parser("queue", help="M7：看队列状态")
    qp.add_argument("--dlq", action="store_true", help="列出死信明细")

    dl = sub.add_parser("dlq", help="M7：死信操作")
    dl.add_argument("action", choices=["replay"], help="replay = 重放")
    dl.add_argument("--id", dest="run_id", required=True,
                    help="链 ID（hub queue --dlq 能看到前 8 位）")

    ev = sub.add_parser("eval", help="M8：跑离线评估套件（有退出码，可接 CI）")
    ev.add_argument("--suite", default="perturbation_v1", help="用例集名（suites/<name>.yaml）")
    ev.add_argument("--strict", action="store_true", help="软门禁也要求 100%")

    args = parser.parse_args()

    # 每次 CLI 调用一个 trace_id：M1~M5 的 span 都挂到它下面
    from hub.core.tracing import print_tree, set_trace_id
    trace_id = set_trace_id()

    if args.cmd == "skeleton":
        cmd_skeleton()
    elif args.cmd == "p0":
        cmd_p0(args.file, args.platform, args.data_type, save=not args.no_save)
    elif args.cmd == "dqe":
        cmd_dqe(force=args.force, batch=args.batch)
    elif args.cmd == "mm":
        cmd_mm(args.package, force=args.force, batch=args.batch,
               limit=args.limit, vision=args.vision)
    elif args.cmd == "topic":
        cmd_topic(force=args.force, limit=args.limit, batch_size=args.batch_size)
    elif args.cmd == "align":
        cmd_align()
    elif args.cmd == "cluster":
        cmd_cluster(max_per_topic=args.max_per_topic)
    elif args.cmd == "price":
        cmd_price()
    elif args.cmd == "priority":
        cmd_priority()
    elif args.cmd == "propose":
        cmd_propose(max_proposals=args.max, execute=not args.dry_run)
    elif args.cmd == "pending":
        cmd_pending()
    elif args.cmd == "approve":
        cmd_approve(args.task_id, args.by, args.note)
    elif args.cmd == "reject":
        cmd_reject(args.task_id, args.by, args.note)
    elif args.cmd == "trace":
        cmd_trace(args.trace_id)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "health":
        cmd_health(getattr(args, "health_trace", None))
    elif args.cmd == "retro":
        cmd_retro(dry_run=args.dry_run, all_=args.all)
    elif args.cmd == "eval":
        # eval 有退出码（CI 用），所以跑完直接 sys.exit
        import sys
        sys.exit(cmd_eval(args.suite, strict=args.strict))
    elif args.cmd == "enqueue":
        import sys
        from hub.queue.commands import enqueue
        sys.exit(enqueue())
    elif args.cmd == "worker":
        import sys
        from hub.queue.commands import worker
        sys.exit(worker(once=args.once, idle=args.idle))
    elif args.cmd == "queue":
        import sys
        from hub.queue.commands import show_queue
        sys.exit(show_queue(dlq=args.dlq))
    elif args.cmd == "dlq":
        import sys
        from hub.queue.commands import replay
        sys.exit(replay(args.run_id))
    else:
        parser.print_help()
        return

    # `hub --trace <cmd>`：跑完把链路树打出来
    if args.trace and args.cmd != "trace":
        print()
        print_tree(trace_id)


if __name__ == "__main__":
    main()
