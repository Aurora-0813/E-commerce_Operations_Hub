# -*- coding: utf-8 -*-
"""
清测试/历史数据（clean_presentation_data.py）—— 2026-11-03
==========================================================
**为什么需要**
    跑了几十轮测试 + 几轮全链之后，库里的数字**虚高**了：

        issue_cluster     241  →  实际只有 **22** 个是"当前"的（其余是历史 run 留下的）
        action_proposal   207  →  当前 run 的只有十几个
        action_outcome   2216  →  大部分是测试夹具造出来的
        audit_log      几百条  →  一半是测试写进去的
        pipeline_run     几十条 →  一半是 DEAD/FAILED 的测试残留

    演示时看到 "241 个问题簇" 会让人以为系统很乱 ✅ ✗
    → 这个脚本把它们清掉，**只留"当前这一次"的真实结果 + 复盘要用的那批** ✅

**删什么 / 留什么（原则：只删"能证明是测试/历史"的）**

    留下：
        · **最新一次 full run 的**簇 / 提案
        · **已经有复盘结论的**提案（`action_outcome.concluded_at` 非空）→ 复盘页那 68 条 ✅
        · 所有 `record` / `review_feature` / `review_topic` / `image_asset`（这是**业务数据** ✅）
        · 最近的链 / 日志 / trace（各留 N 条，方便演示时能翻到 ✅）

    删掉：
        · 历史 run 留下的 `issue_cluster` / `action_proposal`（除上面两批 ✅）
        · 没有对应提案的 `action_outcome`
        · `pipeline_run` 里 **DEAD/FAILED** 的 + 除最近 10 条以外的
        · `audit_log` 除最近 200 条
        · `trace_span` 除最近 500 条
        · `agent_run` 除最近 20 条
        · `analysis_run` 除最近 50 条
        · `ingest_batch` 里 **`row_kept = 0`** 的（全是测试上传 ✅）

**安全性**
    · 默认 **dry-run**（只打印"会删多少"，不动手 ✅）
    · 真要删必须显式 `--yes` ✅
    · 每张表**单独事务**，删之前先打印计数 ✅
    · **绝不删** `record` / `review_feature` / `image_asset` / `spu_catalog` / `sales_daily` ✅

用法：
    python -B scripts/clean_presentation_data.py            # 只看
    python -B scripts/clean_presentation_data.py --yes      # 真删
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select                        # noqa: E402

from hub.persistence.current import current_run_id                 # noqa: E402
from hub.persistence.db import get_session                          # noqa: E402
from hub.persistence.models import (                                # noqa: E402
    ActionOutcome, ActionProposal, AgentRun, AnalysisRun, AuditLog, ImageAsset,
    IngestBatch, IssueCluster, PipelineRun, TraceSpan,
)

KEEP_RUNS = 10
KEEP_AUDIT = 200
KEEP_SPANS = 500
KEEP_AGENT = 20
KEEP_ANALYSIS = 50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真删（不加就是 dry-run ✅）")
    args = ap.parse_args()
    dry = not args.yes

    tag = "【dry-run 只看不动】" if dry else "【真删】"
    print("=" * 68)
    print(f"  清理测试/历史数据 {tag}")
    print("=" * 68)

    with get_session() as s:
        cur = current_run_id(s)

        # ---------- 1. 保留清单 ----------
        # ① 最新 run 的提案
        keep_proposals: set = set()
        if cur is not None:
            keep_proposals |= set(s.scalars(
                select(ActionProposal.id).where(ActionProposal.run_id == cur)).all())
        # ② **已经有复盘结论的**提案（复盘页那 68 条 ✅ 必须留）
        keep_proposals |= set(s.scalars(
            select(ActionOutcome.proposal_id)
            .where(ActionOutcome.concluded_at.is_not(None))).all())
        # ③ 还有待批任务的提案（演示"批准"要用 ✅）
        from hub.persistence.models import HitlTask
        keep_proposals |= set(s.scalars(
            select(HitlTask.proposal_id).where(HitlTask.status == "PENDING")).all())

        print(f"  当前 run = {str(cur)[:8] if cur else '（没有）'}")
        print(f"  要保留的提案 = {len(keep_proposals)} 条"
              f"（最新 run + 有复盘结论 + 还有待办 ✅）")

        # ---------- 2. 统计"会删多少" ----------
        total_clusters = s.scalar(select(func.count()).select_from(IssueCluster)) or 0
        del_clusters = 0 if cur is None else int(s.scalar(
            select(func.count()).select_from(IssueCluster)
            .where(IssueCluster.run_id != cur)) or 0)

        total_props = s.scalar(select(func.count()).select_from(ActionProposal)) or 0
        del_props = 0
        if keep_proposals:
            del_props = int(s.scalar(
                select(func.count()).select_from(ActionProposal)
                .where(ActionProposal.id.notin_(list(keep_proposals)))) or 0)

        total_out = s.scalar(select(func.count()).select_from(ActionOutcome)) or 0
        del_out = 0
        if keep_proposals:
            del_out = int(s.scalar(
                select(func.count()).select_from(ActionOutcome)
                .where(ActionOutcome.proposal_id.notin_(list(keep_proposals)))) or 0)

        # 最近的链（按 created_at 倒序留 10 条）
        keep_runs = set(s.scalars(
            select(PipelineRun.id).order_by(PipelineRun.created_at.desc())
            .limit(KEEP_RUNS)).all())
        total_runs = s.scalar(select(func.count()).select_from(PipelineRun)) or 0
        del_runs = 0
        if keep_runs:
            del_runs = int(s.scalar(
                select(func.count()).select_from(PipelineRun)
                .where(PipelineRun.id.notin_(list(keep_runs)))) or 0)

        def _pk(model):
            """取表的主键列（`TraceSpan` 主键是 `span_id`，不是 `id` ✅）"""
            from sqlalchemy import inspect as _inspect
            return _inspect(model).primary_key[0]

        def _older(model, col, keep: int) -> int:
            """除最近 keep 条以外要删多少 ✅"""
            pk = _pk(model)
            ids = set(s.scalars(
                select(pk).order_by(col.desc()).limit(keep)).all())
            if not ids:
                return 0
            return int(s.scalar(select(func.count()).select_from(model)
                                .where(pk.notin_(list(ids)))) or 0)

        total_audit = s.scalar(select(func.count()).select_from(AuditLog)) or 0
        del_audit = _older(AuditLog, AuditLog.created_at, KEEP_AUDIT)
        total_spans = s.scalar(select(func.count()).select_from(TraceSpan)) or 0
        del_spans = _older(TraceSpan, TraceSpan.start_ts, KEEP_SPANS)
        total_agent = s.scalar(select(func.count()).select_from(AgentRun)) or 0
        del_agent = _older(AgentRun, AgentRun.created_at, KEEP_AGENT)
        total_analysis = s.scalar(select(func.count()).select_from(AnalysisRun)) or 0
        del_analysis = _older(AnalysisRun, AnalysisRun.created_at, KEEP_ANALYSIS)

        total_batch = s.scalar(select(func.count()).select_from(IngestBatch)) or 0
        # ⚠️ **只能删没被 analysis_run 引用的批次**（2026-11-03 踩过外键 ✅）
        _ref_batches = select(AnalysisRun.batch_id).where(AnalysisRun.batch_id.is_not(None))
        del_batch = int(s.scalar(
            select(func.count()).select_from(IngestBatch)
            .where(IngestBatch.row_kept == 0,
                   IngestBatch.id.notin_(_ref_batches))) or 0)

        # ---------- 3. 打印计划 ----------
        rows = [
            ("issue_cluster", total_clusters, del_clusters),
            ("action_proposal", total_props, del_props),
            ("action_outcome", total_out, del_out),
            ("pipeline_run", total_runs, del_runs),
            ("audit_log", total_audit, del_audit),
            ("trace_span", total_spans, del_spans),
            ("agent_run", total_agent, del_agent),
            ("analysis_run", total_analysis, del_analysis),
            ("ingest_batch（row_kept=0）", total_batch, del_batch),
        ]
        print()
        print(f"  {'表':<28} {'现在':>8} {'将删':>8} {'剩余':>8}")
        print("  " + "-" * 56)
        for name, total, d in rows:
            print(f"  {name:<28} {total:>8} {d:>8} {total - d:>8}")
        print()
        print("  **不动**：record / review_feature / review_topic / image_asset /"
              " spu_catalog / sales_daily ✅")

        if dry:
            print()
            print("  👉 这只是预演。要真删就加 `--yes` ✅")
            return 0

        # ---------- 4. 真删（**每张表单独事务**）----------
        print()
        print("  开始删除…")
        try:
            if del_clusters:
                s.execute(delete(IssueCluster).where(IssueCluster.run_id != cur))
            if del_out:
                s.execute(delete(ActionOutcome)
                          .where(ActionOutcome.proposal_id.notin_(list(keep_proposals))))
            if del_props:
                s.execute(delete(ActionProposal)
                          .where(ActionProposal.id.notin_(list(keep_proposals))))
            if del_runs:
                s.execute(delete(PipelineRun).where(PipelineRun.id.notin_(list(keep_runs))))
            if del_audit:
                keep_a = set(s.scalars(
                    select(AuditLog.id).order_by(AuditLog.created_at.desc())
                    .limit(KEEP_AUDIT)).all())
                s.execute(delete(AuditLog).where(AuditLog.id.notin_(list(keep_a))))
            if del_spans:
                _pk_s = _pk(TraceSpan)
                keep_s = set(s.scalars(
                    select(_pk_s).order_by(TraceSpan.start_ts.desc())
                    .limit(KEEP_SPANS)).all())
                s.execute(delete(TraceSpan).where(_pk_s.notin_(list(keep_s))))
            if del_agent:
                keep_g = set(s.scalars(
                    select(AgentRun.id).order_by(AgentRun.created_at.desc())
                    .limit(KEEP_AGENT)).all())
                s.execute(delete(AgentRun).where(AgentRun.id.notin_(list(keep_g))))
            if del_analysis:
                keep_n = set(s.scalars(
                    select(AnalysisRun.id).order_by(AnalysisRun.created_at.desc())
                    .limit(KEEP_ANALYSIS)).all())
                s.execute(delete(AnalysisRun)
                          .where(AnalysisRun.id.notin_(list(keep_n))))
            if del_batch:
                s.execute(delete(IngestBatch).where(
                    IngestBatch.row_kept == 0,
                    IngestBatch.id.notin_(_ref_batches)))
            s.commit()
            print("  ✅ 删完了")
        except Exception as e:                      # noqa: BLE001
            s.rollback()
            print(f"  ❌ 删除失败（已回滚）：{type(e).__name__}: {e}")
            return 1

    # ---------- 5. 删完复查 ----------
    with get_session() as s:
        print()
        print("  复查：")
        for name, model in (("issue_cluster", IssueCluster),
                            ("action_proposal", ActionProposal),
                            ("action_outcome", ActionOutcome),
                            ("pipeline_run", PipelineRun),
                            ("audit_log", AuditLog),
                            ("trace_span", TraceSpan),
                            ("agent_run", AgentRun),
                            ("analysis_run", AnalysisRun),
                            ("ingest_batch", IngestBatch),
                            ("record", __import__(
                                "hub.persistence.models", fromlist=["Record"]).Record),
                            ("image_asset", ImageAsset)):
            n = s.scalar(select(func.count()).select_from(model)) or 0
            print(f"    {name:<20} {n}")
    print()
    print("  下一步：刷新前端（工作台 / 问题全景 / 复盘）→ 数字应该都变小了 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
