# -*- coding: utf-8 -*-
"""
建案例库（build_cases.py）—— 阶段 3.5 的第二步（第一步是读 §8.5 的要求）
=====================================================================
路线图 §8.2 的原话：

    "来源 = `action_outcome`（**只收 POSITIVE / NEGATIVE**，INCONCLUSIVE 不进库）
     拼成一条：问题（簇名+topic+3 条原文）/ 处置（工具+参数摘要）/
               结果（DID+区间+结论）/ 教训（由 reason_code 生成一句人话）
     落 `case_entry`"

**为什么 INCONCLUSIVE 不进库**：
    案例库的用途是"**历史上这么干，结果是这样**" ✅
    INCONCLUSIVE = 不知道结果 → 放进库会让人拿"可能没事"当"有效" ✅ ✗
    （我们的 retro 4 组里有 2 组是 INCONCLUSIVE ✅ → 直接筛掉一半 ✅）

**为什么 embedding 留 NULL**：
    路线图 §8.3 定了"TF-IDF + 余弦（复用 M4）"→ 案例只有几十条，向量没优势 ✅
    → 所以**现在不存向量**，检索时现算 TF-IDF ✅
    （字段留着，等 A/B 后再换 bge-m3 ✅）

**幂等**：按 `source_outcome_id` 去重 ✅（重复跑不会翻倍 ✅）

跑法：python -m scripts.build_cases
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                        # noqa: E402

from hub.persistence.db import get_session           # noqa: E402
from hub.persistence.models import (ActionOutcome,   # noqa: E402
                                    ActionProposal, CaseEntry,
                                    ClusterMember, IssueCluster, Record)

# 只收有明确结论的 ✅
KEEP_CONCLUSIONS = ("POSITIVE", "NEGATIVE")
MAX_TEXTS = 3          # 案例里放几条原文
MAX_ARG_CHARS = 120    # 参数摘要长度
MAX_TEXT_CHARS = 80    # 每条原文截断


def _lesson(conclusion: str, reason_code: str | None) -> str:
    """由 `reason_code` 生成一句"教训"（人话 ✅）。

    **为什么不用 LLM 写**：教训要**稳定、可复核** ✅
        LLM 每次写的都不一样，案例库会越攒越乱 ✅
        而 reason_code 是 M9 已经定义好的枚举 ✅
    """
    table = {
        "SIGNIFICANT_IMPROVEMENT": "这个做法有效，同类问题可以优先考虑",
        "SIGNIFICANT_WORSENING": "这个做法让情况变差，同类问题要避开",
        "REFUND_WORSENED": "差评降了但退款升了 —— 只安抚了情绪，没解决货的问题",
        "NO_SIGNIFICANT_CHANGE": "没有显著变化，别把这招当成有效",
        "CONTROL_INSUFFICIENT": "对照组太少，结论不可信",
        "PROMO_CONFOUNDED": "撞上大促，效果说不清",
    }
    if reason_code and reason_code in table:
        return table[reason_code]
    return ("这个做法有效" if conclusion == "POSITIVE" else "这个做法有害")


def _args_digest(tool_name: str, args: dict | None) -> str:
    """参数摘要（**不放全文**，只放能复述的要点 ✅）。"""
    a = args or {}
    keep = {k: v for k, v in a.items()
            if k in ("platform", "spu_id", "cluster_id", "n", "new_price")}
    s = f"{tool_name}(" + ", ".join(f"{k}={str(v)[:30]}" for k, v in keep.items()) + ")"
    return s[:MAX_ARG_CHARS]


def build() -> dict:
    """建/更新案例库。返回统计。"""
    stats = {"scanned": 0, "kept": 0, "created": 0, "updated": 0, "skipped": 0}
    with get_session() as s:
        # ---- 1. 取有明确结论的 outcome ✅ ----
        outs = s.scalars(select(ActionOutcome).where(
            ActionOutcome.conclusion.in_(KEEP_CONCLUSIONS))).all()
        stats["scanned"] = len(outs)

        for o in outs:
            prop = s.get(ActionProposal, o.proposal_id)
            if prop is None:
                stats["skipped"] += 1
                continue

            # ---- 2. 找这个提案对应的簇（从 evidence_refs 里取 ✅）----
            # ⚠️ `evidence_refs` 里的 `issue_cluster` 是**字符串** ✅
            #    而 `IssueCluster.id` 是 UUID 主键 ✅
            #    → `s.get(IssueCluster, "uuid字符串")` **查不到**（类型不匹配）✅ ✗
            #    踩过：20 条全被跳过，日志写"没有簇/提案" ✅
            from uuid import UUID as _UUID
            cid = None
            for ref in (prop.evidence_refs or []):
                if isinstance(ref, dict) and ref.get("issue_cluster"):
                    cid = ref["issue_cluster"]
                    break
            cluster = None
            if cid:
                try:
                    cluster = s.get(IssueCluster, _UUID(str(cid)))
                except (ValueError, TypeError):
                    cluster = None
            if cluster is None:
                stats["skipped"] += 1
                continue

            # ---- 3. 代表评价原文（前 3 条 ✅）----
            member_ids = [m.record_id for m in s.scalars(
                select(ClusterMember).where(ClusterMember.cluster_id == cluster.id)
                .order_by(ClusterMember.is_representative.desc())).all()[:MAX_TEXTS]]
            texts = []
            if member_ids:
                rows = s.execute(
                    select(Record.content_clean).where(Record.id.in_(member_ids))).all()
                texts = [(r[0] or "")[:MAX_TEXT_CHARS] for r in rows if r[0]]
            representative = " / ".join(texts) or "（无原文）"

            # ---- 4. 拼案例 ✅ ----
            lesson = _lesson(o.conclusion or "", o.reason_code)
            values = {
                "cluster_label": cluster.label,
                "root_cause": prop.topic or cluster.business_domain or "",
                "business_domain": cluster.business_domain,
                "platforms": list(cluster.platforms or []),
                "action_taken": _args_digest(prop.tool_name, prop.args),
                "outcome_conclusion": o.conclusion,
                "effect_size": float(o.effect) if o.effect is not None else None,
                "representative_text": representative,
                "status": "ACTIVE",
                # 教训放哪：`case_entry` 没有 lesson 列 ✅
                #   → 塞进 `root_cause`？不行，那是"根因" ✅ ✗
                #   → 塞进 `action_taken` 末尾？会污染"处置" ✅ ✗
                #   → **拼进 `representative_text` 前缀** ✅
                #     检索时这段也会参与 TF-IDF，正好让"教训"可被关键词命中 ✅
            }
            values["representative_text"] = f"【教训】{lesson}｜{representative}"

            # ---- 5. 幂等：按 source_outcome_id ✅ ----
            exist = s.scalars(select(CaseEntry).where(
                CaseEntry.source_outcome_id == o.id)).first()
            if exist is not None:
                for k, v in values.items():
                    setattr(exist, k, v)
                stats["updated"] += 1
            else:
                # ⚠️ `case_entry.case_id` 是 **UUID 主键**（2026-11-01 核对）✅
                #    不能拼字符串（`s.get(..., "case-xxx")` / INSERT 都会失败 ✅）
                from uuid import uuid4
                s.add(CaseEntry(case_id=uuid4(),
                                source_outcome_id=o.id, **values))
                stats["created"] += 1
            stats["kept"] += 1
        s.commit()
    return stats


def main() -> int:
    print("=" * 66)
    print("  建案例库（阶段 3.5）—— 只收 POSITIVE / NEGATIVE")
    print("=" * 66)
    try:
        st = build()
    except Exception as e:                            # noqa: BLE001
        import traceback
        print(f"  ❌ 失败：{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    print(f"  扫描 outcome：{st['scanned']} 条")
    print(f"  收进案例库：{st['kept']} 条（新建 {st['created']} / 更新 {st['updated']}）")
    print(f"  跳过（没有簇/提案）：{st['skipped']} 条")
    print()
    print("  ⚠️ INCONCLUSIVE 的 outcome **不进库**（路线图 §8.2）✅")
    print("  ⚠️ embedding 留 NULL —— 检索用 TF-IDF 现算（§8.3）✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
