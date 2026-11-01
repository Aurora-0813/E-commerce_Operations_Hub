# -*- coding: utf-8 -*-
"""
Case RAG 测试（check_case_rag.py）—— 阶段 3.5
===========================================
路线图 §8.5 要求 4 条：
    ① 案例数 > 0 且只含明确结论
    ② 检索能命中同 topic
    ③ **伪造 case_id → 必须被丢弃**
    ④ walk-forward 链路跑通

**当前数据状态（2026-11-01）**：案例库是空的 ✅
    （真实干预还在观察窗口里，最早 2026-11-13 出结果）
    → 所以 ① 用 **fixture** 验（用完即删，不进真实库 ✅）
    → 并额外验"空库时返回空列表"（这才是现在的正确行为 ✅）
"""
import sys
from pathlib import Path
from uuid import uuid4 as _u4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select          # noqa: E402

from hub.persistence.db import get_session           # noqa: E402
from hub.persistence.models import CaseEntry         # noqa: E402

FAILS: list[str] = []
# ⚠️ `case_entry.case_id` 是 **UUID 主键**（2026-11-01 核对）✅
#    第一版用 `"case-test-a"` 这类字符串 → INSERT/DELETE 报
#    `操作符不存在: uuid ~~ uuid` ✅ ✗ → 现在用真 UUID ✅
CASE_A, CASE_B, CASE_C = _u4(), _u4(), _u4()
MINE = [CASE_A, CASE_B, CASE_C]
# ⚠️ fixture 用**固定标记**，这样即使上次跑崩了也能清干净 ✅
#    （第一版只清 MINE 里的 3 个 UUID → 崩溃后残留，下次跑就假红 ✅）
TAG = "【教训】test-fixture"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cleanup() -> None:
    from sqlalchemy import or_
    with get_session() as s:
        s.execute(delete(CaseEntry).where(or_(
            CaseEntry.case_id.in_(MINE),
            CaseEntry.representative_text.like(TAG + "%"),
        )))
        s.commit()


def seed() -> None:
    """3 条 fixture：同 topic 的 2 条（quality）+ 别的 topic 1 条（logistics）✅"""
    with get_session() as s:
        s.add_all([
            CaseEntry(case_id=CASE_A, cluster_label="掉色严重",
                      root_cause="quality", business_domain="女装", platforms=["jd"],
                      action_taken="listing.update_desc(spu_id=TB-0001)",
                      outcome_conclusion="POSITIVE", effect_size=-0.21,
                      representative_text=TAG + "｜有效｜洗两次掉色严重 水都变色了",
                      status="ACTIVE"),
            CaseEntry(case_id=CASE_B, cluster_label="起球严重",
                      root_cause="quality", business_domain="女装", platforms=["taobao"],
                      action_taken="review.draft_reply(cluster_id=x)",
                      outcome_conclusion="NEGATIVE", effect_size=0.12,
                      representative_text=TAG + "｜有害｜穿一次就起球 面料很差",
                      status="ACTIVE"),
            CaseEntry(case_id=CASE_C, cluster_label="物流延迟",
                      root_cause="logistics", business_domain="女装", platforms=["jd"],
                      action_taken="review.draft_reply(cluster_id=y)",
                      outcome_conclusion="POSITIVE", effect_size=-0.08,
                      representative_text=TAG + "｜有效｜快递太慢 等了十天",
                      status="ACTIVE"),
        ])
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  Case RAG（阶段 3.5）")
    print("=" * 66)

    from hub.rag.case_rag import (MIN_SIMILARITY, render_cases, retrieve,
                                  verify_citations)

    cleanup()

    # ---------- [0] 空库是正常路径 ----------
    print("\n[0] 空库 → 返回 []（不是报错）")
    r = retrieve("掉色严重 洗两次掉色", topic="quality")
    check("空库返回 []", r == [], str(r))
    check("render 空列表返回空串", render_cases([]) == "")

    seed()

    # ---------- [1] 只含明确结论 ----------
    print("\n[1] 案例库只含 POSITIVE / NEGATIVE")
    with get_session() as s:
        n = s.scalar(select(func.count()).select_from(CaseEntry)
                     .where(CaseEntry.status == "ACTIVE")) or 0
        bad = s.scalar(select(func.count()).select_from(CaseEntry)
                       .where(CaseEntry.outcome_conclusion.notin_(
                           ("POSITIVE", "NEGATIVE")))) or 0
    check("有活跃案例", n >= 3, f"{n} 条")
    check("没有别的结论（如 INCONCLUSIVE）", bad == 0, f"{bad} 条")

    # ---------- [2] 检索命中同 topic ----------
    print("\n[2] 检索命中同 topic，别的 topic 被过滤")
    hits = retrieve("掉色严重 洗两次掉色 水都变色了", topic="quality")
    check("有命中", len(hits) > 0, f"{len(hits)} 条")
    check("全部是 quality", all(h["root_cause"] == "quality" for h in hits),
          str([h["root_cause"] for h in hits]))
    check("没有 logistics 混进来",
          all(h["root_cause"] != "logistics" for h in hits))
    check("按 rank_score 降序",
          all(hits[i]["rank_score"] >= hits[i + 1]["rank_score"]
              for i in range(len(hits) - 1)),
          str([h["rank_score"] for h in hits]))
    check("相似度达标", all(h["similarity"] >= MIN_SIMILARITY for h in hits),
          str([h["similarity"] for h in hits]))
    if hits:
        # ⚠️ `retrieve()` 返回的 case_id 是 **str**（源码里转过了 ✅），
        #    而 `CASE_A` 是 UUID 对象 → 必须两边都转 str 再比 ✅
        check("最像的那条排第一", str(hits[0]["case_id"]) == str(CASE_A),
              str(hits[0]["case_id"]))

    # ---------- [3] 伪造 case_id 被丢弃 ----------
    print("\n[3] 伪造 case_id → 必须被丢弃（只丢该引用 ✅）")
    fake = str(_u4())
    ok, badc = verify_citations([str(CASE_A), fake], hits)
    check("有效的保留", str(CASE_A) in ok, str(ok))
    check("伪造的被丢弃", fake in badc, str(badc))
    check("有效的不受影响（不整条作废）", len(ok) >= 1, str(ok))

    # ---------- [4] render + walk-forward ----------
    print("\n[4] render 出「参考案例」块；walk-forward 能找回自己")
    block = render_cases(hits)
    check("渲染出块", bool(block), block[:50].replace("\n", " "))
    check("块里带 case_id", str(CASE_A) in block)
    check("块里说了只能引用 case_id", "只能引用" in block)
    hit2 = retrieve("穿一次就起球 面料很差", topic="quality")
    check("walk-forward 能找回自己",
          any(str(h["case_id"]) == str(CASE_B) for h in hit2),
          str([str(h["case_id"]) for h in hit2]))

    cleanup()
    print("\n（fixture 已清理 ✅）")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
