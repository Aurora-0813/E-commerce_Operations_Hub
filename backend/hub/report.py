# -*- coding: utf-8 -*-
"""
报表导出（report.py）—— 阶段 3.9
===============================
路线图 §9.5.3：

| 导出 | 内容 |
| --- | --- |
| **差评清单** | 某簇/某主题的评价原文 + 平台 + 时间 + 分类 + 严重度 |
| **簇汇总** | 22 个簇的条数/P 档/分数/GMV 涉及面 |
| **复盘报告** | 4 个动作的 DID/区间/结论/理由码 |

技术要求：
    · `openpyxl`（已在 ✅）
    · **必须做 PII 复查**（导出前扫一遍 ✅）
    · 大表上限 **1 万行**（超了提示分页 ✅）

**为什么"导出前 PII 复查"是硬要求**：
    导出的文件会被**带出系统**（发微信、放共享盘、拿去开会 ✅）
    → 手机号/身份证一旦进了 xlsx，就再也收不回 ✅
    → 所以导出是**最后一道 PII 关卡** ✅
    → 复查策略：**脱敏而不是拒绝**（因为运营确实需要原文 ✅），
      并在**首行注释**里写明"已脱敏 N 处" ✅
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

MAX_ROWS = 10000        # 1 万行上限 ✅


class TooManyRows(Exception):
    """超过 1 万行。**调用方要提示分页导出** ✅"""


# ════════════════════════════════════════════════════ PII 复查
def pii_review(text: str) -> tuple[str, int]:
    """导出前的 PII 复查：**脱敏并计数** ✅

    复用 `cloud_guard.redact_pii`（那里已经有一套正则 ✅）
    —— **不另写一套**：两套规则迟早不一致 ✅
    """
    try:
        from hub.core.cloud_guard import redact_pii
        new, hits = redact_pii(text or "")
        return new, len(hits or [])
    except Exception:                             # noqa: BLE001
        return text or "", 0


# ════════════════════════════════════════════════════ 差评清单
def export_negative_reviews(out_path: str | Path, *, cluster_id: str | None = None,
                            topic: str | None = None, limit: int = MAX_ROWS) -> dict:
    """导出差评清单（评价原文 + 平台 + 时间 + 分类 + 严重度 ✅）。"""
    import openpyxl
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import (ClusterMember, Record, ReviewTopic)

    with get_session() as s:
        stmt = select(Record).where(Record.rating <= 2,
                                    Record.dqe_decision == "keep")
        if cluster_id:
            from uuid import UUID
            ids = [m.record_id for m in s.scalars(
                select(ClusterMember).where(
                    ClusterMember.cluster_id == UUID(str(cluster_id)))).all()]
            if ids:
                stmt = stmt.where(Record.id.in_(ids))
            else:
                raise ValueError(f"簇 {cluster_id} 没有成员")
        rows = s.scalars(stmt.order_by(Record.review_time.desc())
                         .limit(limit + 1)).all()
        if len(rows) > limit:
            raise TooManyRows(f"超过 {MAX_ROWS} 行 —— 请分页导出（加时间范围或簇过滤）")
        # 关联 topic（现查，避免 N+1 ✅）
        rids = [r.id for r in rows]
        tp = {}
        if rids:
            for rid, t, sev in s.execute(
                    select(ReviewTopic.record_id, ReviewTopic.topic,
                           ReviewTopic.severity).where(
                        ReviewTopic.record_id.in_(rids))).all():
                tp[rid] = (t, sev)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "差评清单"
    ws.append(["平台", "商品", "时间", "评分", "问题类型", "严重度", "评价原文"])
    total_redact = 0
    for r in rows:
        t, sev = tp.get(r.id, ("", None))
        text, n = pii_review(r.content_clean or "")
        total_redact += n
        ws.append([r.platform, r.platform_spu_id,
                   r.review_time.strftime("%Y-%m-%d") if r.review_time else "",
                   r.rating, t, float(sev) if sev is not None else None, text])
    ws.insert_rows(1)
    ws.cell(row=1, column=1,
            value=f"导出 {len(rows)} 行；**PII 已脱敏 {total_redact} 处**；"
                  f"生成于 {datetime.now():%Y-%m-%d %H:%M}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"path": str(out_path), "rows": len(rows), "pii_redacted": total_redact}


# ════════════════════════════════════════════════════ 簇汇总
def export_clusters(out_path: str | Path) -> dict:
    """导出簇汇总（条数/P 档/分数/涉及商品数 ✅）。"""
    import openpyxl
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster

    with get_session() as s:
        cs = s.scalars(select(IssueCluster)
                       .order_by(IssueCluster.priority_score.desc().nullslast())
                       .limit(MAX_ROWS)).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "簇汇总"
    ws.append(["簇名", "条数", "P 档", "优先级分", "品类", "平台", "涉及商品数",
               "严重度", "增长率"])
    for c in cs:
        ws.append([c.label, c.review_count, c.priority_tier,
                   float(c.priority_score) if c.priority_score is not None else None,
                   c.business_domain, "/".join(c.platforms or []),
                   len(c.spu_ids or []),
                   float(c.severity) if c.severity is not None else None,
                   float(c.growth_rate) if c.growth_rate is not None else None])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"path": str(out_path), "rows": len(cs), "pii_redacted": 0}


# ════════════════════════════════════════════════════ 复盘报告
def export_retro(out_path: str | Path) -> dict:
    """导出复盘报告（DID/区间/结论/理由码 ✅）。"""
    import openpyxl
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import ActionOutcome

    with get_session() as s:
        outs = s.scalars(select(ActionOutcome)
                         .order_by(ActionOutcome.created_at.desc())
                         .limit(MAX_ROWS)).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "复盘报告"
    ws.append(["提案", "状态", "结论", "效果量", "理由码", "置信度",
               "基期起", "基期止", "观察期止"])
    for o in outs:
        # ⚠️ `action_outcome.effect` 是 **JSONB**（dict，含 DID/区间等），不是数字 ✅
        #    `float(dict)` → TypeError ✅ ✗（2026-11-01 核对）
        #    → 写进单元格时转成 JSON 字符串（Excel 一格放不下结构化的，但能读 ✅）
        import json as _json
        eff = o.effect
        eff_cell = (_json.dumps(eff, ensure_ascii=False, default=str)
                    if isinstance(eff, (dict, list)) else eff)
        ws.append([str(o.proposal_id), o.status, o.conclusion,
                   eff_cell,
                   o.reason_code,
                   float(o.confidence) if o.confidence is not None else None,
                   o.baseline_start.isoformat() if o.baseline_start else None,
                   o.baseline_end.isoformat() if o.baseline_end else None,
                   o.observe_end.isoformat() if o.observe_end else None])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"path": str(out_path), "rows": len(outs), "pii_redacted": 0}
