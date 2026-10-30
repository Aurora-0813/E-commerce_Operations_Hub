# -*- coding: utf-8 -*-
"""
最近一次运行的健康度（health.py）—— `hub health`
=================================================
回答：**"这一次跑得怎么样"**。给工程 / 排障看。

数据来源（**不新埋点，全是已有的**）：
    耗时 / token      ← `trace_span`（最近一次**全链**的 trace）
    Vision 调用次数   ← `trace_span` 里名字为 `vision.extract` 的 span
    兜底 / 失败计数    ← `analysis_run.stats`（M3 自己记的）
    回查通过率 / 死信  ← `tool_execution` / `dead_letter`

**为什么不从 span 里读兜底率**：
    "关键词兜底"是被 catch 住的分支，span 状态还是 OK——
    span 能记"调了几次、花了多久"，但"业务上兜底了几条"只有业务自己知道。
    所以：**span 管时序，analysis_run 管业务计数**。
"""
from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import (
    AnalysisRun,
    DeadLetter,
    ToolExecution,
    TraceSpan,
)


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def _latest_stats(s, module: str) -> dict:
    """取某模块**最近一次真正干了活**的 stats，统一返回平铺的 summary。

    ⚠️ 两个模块的结构**不一样**（历史原因，见工程规范的推迟清单）：
        M3 → {"module":"M3", "summary":{...}, "attributions":[...]}   ← 嵌套
        M2 → {"module":"M2", "images":172, "failed":0, ...}           ← 平铺

    所以**必须走这个函数**，不要直接掏 `stats["xxx"]`——
    直接掏的话 M3 的字段永远查不到（实测踩过：兜底率显示 0/0）。

    另外：M3 的增量空跑会写一条 `classified=0` 的记录，
    拿它来算兜底率会显示 "0/1"，看着像数据坏了。所以挑 `classified>0` 的那次。
    """
    runs = s.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.stats["module"].astext == module)
        .order_by(AnalysisRun.created_at.desc())
        .limit(10)
    ).all()
    found: dict = {}
    for run in runs:
        st = run.stats or {}
        summary = st.get("summary") or st       # M3 取嵌套 summary，M2 直接就是平的
        if module != "M3" or summary.get("classified", 0) > 0:
            return summary
        found = found or summary
    return found


def _pick_trace(s, trace_id: str | None = None) -> str | None:
    """挑一条 trace。

    :param trace_id: 指定就用它
    :default: 挑**最近一次"像全链"的**（顶层 span ≥ 2 个）。
              为什么：单跑 `hub mm` 也会产生 trace，只有一条 m2.multimodal，
              拿它当"最近一次运行"会让耗时报表只剩一行（实测踩过）。
    """
    if trace_id:
        return trace_id
    rows = s.execute(
        select(TraceSpan.trace_id,
               func.count(TraceSpan.span_id),
               func.count(TraceSpan.span_id).filter(TraceSpan.parent_id.is_(None)),
               func.max(TraceSpan.end_ts))
        .group_by(TraceSpan.trace_id)
        .order_by(func.max(TraceSpan.end_ts).desc())
        .limit(10)
    ).all()
    for tid, _, n_top, _ in rows:
        if n_top >= 2:                 # 至少两步 = 像一次链
            return tid
    return rows[0][0] if rows else None


def collect(trace_id: str | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """收集运行指标，返回 [(块名, [(指标名, 值), ...]), ...]。"""
    with get_session() as s:
        tid = _pick_trace(s, trace_id)

        steps: list[tuple[str, float, int, int]] = []
        vision_n, vision_tok = 0, 0
        total_in = total_out = 0
        total_s = 0.0

        if tid:
            rows = s.scalars(select(TraceSpan)
                             .where(TraceSpan.trace_id == tid)).all()
            # 建父子索引，用来算"某一步的**子树** token"（自身往往没 token）
            kids: dict[str | None, list] = {}
            for r in rows:
                kids.setdefault(r.parent_id, []).append(r)

            def subtree_tokens(span_id: str) -> tuple[int, int]:
                tin = tout = 0
                stack = [span_id]
                while stack:
                    cur = stack.pop()
                    for r in rows:
                        if r.span_id == cur:
                            tin += r.tokens_in or 0
                            tout += r.tokens_out or 0
                    stack.extend(c.span_id for c in kids.get(cur, []))
                return tin, tout

            for r in kids.get(None, []):
                if not (r.start_ts and r.end_ts):
                    continue
                dur = (r.end_ts - r.start_ts).total_seconds()
                tin, tout = subtree_tokens(r.span_id)
                steps.append((r.name, dur, tin, tout))
            steps.sort(key=lambda x: -x[1])

            total_in = sum(r.tokens_in or 0 for r in rows)
            total_out = sum(r.tokens_out or 0 for r in rows)
            total_s = sum(x[1] for x in steps)

            vis = [r for r in rows if r.name == "vision.extract"]
            vision_n = len(vis)
            vision_tok = sum(r.tokens_in or 0 for r in vis)

        # ---------- 业务计数（analysis_run.stats）----------
        m3s = _latest_stats(s, "M3")
        m2s = _latest_stats(s, "M2")

        n_tool = s.scalar(select(func.count()).select_from(ToolExecution)) or 0
        n_verify_ok = s.scalar(select(func.count()).select_from(ToolExecution)
                               .where(ToolExecution.verify_ok.is_(True))) or 0
        n_verify_bad = s.scalar(select(func.count()).select_from(ToolExecution)
                                .where(ToolExecution.verify_ok.is_(False))) or 0
        n_denied = s.scalar(select(func.count()).select_from(ToolExecution)
                            .where(ToolExecution.deny_code.is_not(None))) or 0
        n_dead = s.scalar(select(func.count()).select_from(DeadLetter)) or 0

    # ==================== ① 耗时 ====================
    perf = []
    if steps:
        for name, dur, _, _ in steps:
            perf.append((name, f"{dur:7.1f}s  （{dur / total_s * 100:4.1f}%）"))
        perf.append(("**总耗时**", f"{total_s:7.1f}s"))
    else:
        perf.append(("（没有 trace）", "先跑一次 run_all"))

    # ==================== ② 成本 ====================
    cost = [
        ("token（入/出）", f"{total_in:,} / {total_out:,}"),
        ("输入输出比", f"{total_in / total_out:.1f} : 1" if total_out else "—"),
        ("每步 token（含子调用）",
         "｜".join(f"{n.replace('.', '_')} {i + o:,}" for n, _, i, o in steps[:4]) or "—"),
        ("Vision 调用", f"{vision_n} 次"),
        ("每图 token", f"{vision_tok // vision_n:,}" if vision_n else "—"),
    ]

    # ==================== ③ 健康 ====================
    fallback = m3s.get("fallback", 0)
    classified = m3s.get("classified", 0)
    failed_cells = m3s.get("failed_cells", 0)
    skipped_cells = m3s.get("skipped_cells", 0)
    evidence = m3s.get("evidence_total", 0)
    dropped = m3s.get("evidence_dropped", 0)
    vision_failed = m2s.get("failed", 0)

    healthy = [
        ("兜底率", f"{fallback}/{classified} = {_pct(fallback, classified)}"
                   f"（关键词兜底，越高越不可信）"),
        ("归因失败", f"{failed_cells} 个格子（样本不足跳过 {skipped_cells} 个）"),
        ("证据 grounded", f"{evidence} 条证据，丢弃编造 {dropped} 条 → "
                          f"{_pct(evidence, evidence + dropped)}"),
        ("Vision 失败", f"{vision_failed} 张"),
        ("工具调用", f"{n_tool} 次（被拒 {n_denied}）"),
        ("回查通过率", f"{_pct(n_verify_ok, n_verify_ok + n_verify_bad)}"
                       f"（通过 {n_verify_ok}／不一致 {n_verify_bad}）"),
        ("死信", f"{n_dead} 条"),
        ("trace", tid or "—"),
    ]

    return [("耗时", perf), ("成本", cost), ("健康", healthy)]
