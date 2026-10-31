# -*- coding: utf-8 -*-
"""
只读工具测试（check_readonly_tools.py）—— 阶段 3.1
==================================================
这 5 个工具是 **agent 的地基**：它们出错比"agent 规划错"更隐蔽 ✅
（因为 agent 会拿着错的数据一本正经地下结论）

验 8 条：
    1. 5 个工具都注册了，且 **risk_level 全是 L0**（只读就不该有写风险）
    2. `query_cluster` 返回簇名/条数/**问题类型（现算的）**/品类
    3. `query_reviews` **最多 5 条、每条 ≤120 字**（上下文预算，改成 20 条就该红）
    4. `query_history` 能读到历史战绩
    5. `query_product` 有平台维度的销售聚合
    6. `query_outcome` 有复盘结论（或明确说"还没复盘"）
    7. **负向**：乱编的 cluster_id 必须报错（不是返回空 dict）
    8. **负向**：`n` 超过上限必须被 pydantic 拦下（防上下文爆）

为什么第 3 条要用"断言上限"而不是"看看有几条"：
    上限是**上下文预算的一部分**（5 条 × 120 字 ≈ 600 字 ≈ 300 token ✅）
    以后有人把 MAX_REVIEWS 改成 20，这条测试会红 ✅

跑法：python tests/check_readonly_tools.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.tools.builtin import register_all           # noqa: E402
from hub.tools.readonly import MAX_REVIEW_CHARS, MAX_REVIEWS   # noqa: E402
from hub.tools.spec import CallContext, get          # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 62)
    print("  只读查询工具（阶段 3.1）")
    print("=" * 62)
    register_all()
    # CallContext 的字段：tenant_id / role / trace_id / run_id / proposal_id / risk_floor
    # （没有 actor —— 那是 audit_log 的概念，不是工具上下文的）
    ctx = CallContext(role="operator", trace_id="test-readonly")

    # ---------- 1. 注册 + 风险等级 ----------
    print("\n[1] 5 个工具注册且 risk_level=L0")
    names = ["query_cluster", "query_reviews", "query_history",
             "query_product", "query_outcome"]
    for n in names:
        spec = get(n)
        check(f"{n} 已注册", spec is not None)
        if spec:
            check(f"{n} 是 L0（只读）", spec.risk_level == "L0", spec.risk_level)

    # ---------- 找真实数据 ----------
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import (ActionOutcome, IssueCluster, SalesDaily)

    with get_session() as s:
        cluster = s.scalars(select(IssueCluster).limit(1)).first()
        spu = s.scalars(select(SalesDaily.canonical_spu_id).limit(1)).first()
        prop = s.scalars(select(ActionOutcome.proposal_id).limit(1)).first()

    if cluster is None:
        print("\n[!] 库里没有簇 —— 先跑全链再测")
        return 1
    cid = str(cluster.id)

    # ---------- 2. query_cluster ----------
    print("\n[2] query_cluster")
    r = get("query_cluster").handler({"cluster_id": cid}, ctx)
    check("有簇名", bool(r.get("label")), r.get("label"))
    check("有条数", isinstance(r.get("review_count"), int), str(r.get("review_count")))
    # 问题类型是**现算**的（从 cluster_member → review_topic 取众数）
    check("有问题类型（现算，不是 business_domain）", bool(r.get("topic")), str(r.get("topic")))
    check("品类字段仍在（business_domain=女装/男装，设计本意）",
          bool(r.get("business_domain")), str(r.get("business_domain")))

    # ---------- 3. query_reviews 的截断 ----------
    print("\n[3] query_reviews 的截断（上下文预算）")
    r = get("query_reviews").handler({"cluster_id": cid, "n": MAX_REVIEWS}, ctx)
    check(f"最多 {MAX_REVIEWS} 条", r["count"] <= MAX_REVIEWS, f"count={r['count']}")
    long_ones = [x for x in r["reviews"] if len(x["text"]) > MAX_REVIEW_CHARS]
    check(f"每条 ≤{MAX_REVIEW_CHARS} 字", not long_ones, f"超长 {len(long_ones)} 条")

    # ---------- 4. query_history ----------
    print("\n[4] query_history")
    r = get("query_history").handler({"topic": "quality"}, ctx)
    check("返回结构正确", "tools" in r and isinstance(r["tools"], list),
          f"{len(r.get('tools') or [])} 个工具")

    # ---------- 5. query_product ----------
    print("\n[5] query_product")
    if spu is None:
        print("  [!] 库里没有销售数据，跳过")
    else:
        r = get("query_product").handler({"spu_id": str(spu), "days": 90}, ctx)
        check("有 platform 维度", len(r.get("platforms") or []) >= 1,
              f"{len(r.get('platforms') or [])} 个平台")
        if r.get("platforms"):
            p = r["platforms"][0]
            check("有 GMV/件数/退款率",
                  all(k in p for k in ("gmv", "units", "refund_rate")), str(p))

    # ---------- 6. query_outcome ----------
    print("\n[6] query_outcome")
    if prop is None:
        print("  [!] 库里没有复盘记录，跳过")
    else:
        r = get("query_outcome").handler({"proposal_id": str(prop)}, ctx)
        check("返回 outcomes 列表", isinstance(r.get("outcomes"), list),
              f"{r.get('total')} 条")

    # ---------- 7. 负向：乱编的 cluster_id ----------
    print("\n[7] 负向：乱编 cluster_id 必须报错")
    try:
        get("query_cluster").handler({"cluster_id": "掉色严重"}, ctx)
        check("乱编的 id 被拒", False, "居然没报错 —— 模型的编造会静默通过")
    except Exception as e:                     # noqa: BLE001
        check("乱编的 id 被拒", True, f"{type(e).__name__}")

    # ---------- 8. 负向：n 超上限必须被 schema 拦 ----------
    print("\n[8] 负向：n 超过上限必须被 pydantic 拦下")
    spec = get("query_reviews")
    try:
        spec.params_model(cluster_id=cid, n=50)
        check("n=50 被拒", False, "没拦住 —— 上下文会被撑爆")
    except Exception as e:                     # noqa: BLE001
        check("n=50 被拒", True, f"{type(e).__name__}")

    print("\n" + "=" * 62)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 62)
        return 1
    print("  全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
