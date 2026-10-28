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
    print(f"归因：格子 {stats.cells} 个，成功 {stats.attributed}，"
          f"跳过 {stats.skipped_cells}")
    print(f"  证据 {stats.evidence_total} 条，丢弃编造 {stats.evidence_dropped} 条")
    print(f"  耗时 {stats.elapsed_s}s｜trace_id = {stats.trace_id}")


def main() -> None:
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(prog="hub", description="跨平台电商 AI 运营决策中枢 CLI")
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

    args = parser.parse_args()

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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
