# -*- coding: utf-8 -*-
"""
检查点 B：网关四道防线 + 风险分级

验 11 件事：
    ① 未知工具 → TOOL_NOT_FOUND
    ② 参数非法（多余字段）→ PARAM_INVALID
    ③ 角色不够（L2 用 system）→ SCOPE_DENIED
    ④ 依据缺失 → EVIDENCE_MISSING
    ⑤ 依据不存在 → EVIDENCE_NOT_FOUND
    ⑥ 拿 A 簇的证据改 B 商品 → EVIDENCE_MISMATCH
    ⑦ L0 执行 + 回查通过
    ⑧ L1 执行 + 回查通过
    ⑨ 幂等：同样的调用第二次 → REPLAYED（不重复执行）
    ⑩ L2 → PENDING_APPROVAL，**且真的没改数据**
    ⑪ L3 → PENDING_HITL，**且真的没改价格**
    ⑫ 频控：连打 12 次不同参数 → 出现 RATE_LIMITED

运行（在 backend/ 目录下）：
    python tests/check_gateway.py
"""
import sys
import uuid
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                       # noqa: E402

import hub.tools                                    # noqa: E402,F401  (注册工具)
from hub.gateway import (                           # noqa: E402
    LIMITER,
    Decision,
    DenyCode,
    ToolCall,
    invoke,
)
from hub.gateway.checks import idem_key               # noqa: E402
from hub.persistence.db import get_session            # noqa: E402
from hub.persistence.models import (                  # noqa: E402
    ActionProposal,
    AnalysisRun,
    ClusterMember,
    SpuCatalog,
)
from hub.tools.spec import CallContext, get           # noqa: E402

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def make_run_and_proposal() -> str:
    """建一个 analysis_run + action_proposal，返回 proposal_id。

    tool_execution.proposal_id 是 NOT NULL 外键，所以每次调用都要挂一个提案。
    """
    with get_session() as s:
        run = AnalysisRun(trace_id=f"gw-test-{uuid.uuid4().hex[:8]}",
                          tenant_id="default", status="RUNNING")
        s.add(run)
        s.flush()
        p = ActionProposal(
            run_id=run.id,
            trace_id=run.trace_id,
            tool_name="catalog.query_price",
            tool_version="1.0.0",
            args={},
            risk_level="L0",
            evidence_refs=[],
            dry_run=True,
            status="PROPOSED",
        )
        s.add(p)
        s.flush()
        pid = str(p.id)
        s.commit()
    return pid


def clear_idempotency() -> int:
    """清空幂等记录。

    为什么测试要清：幂等键的窗口是"今天"，上一次跑测试留下的记录会让
    这一次的"首次执行"直接变成"重放"——**测试就不可重复了**。
    生产上这是正确行为（同一动作一天内只做一次），但测试必须从干净状态开始。
    """
    from sqlalchemy import delete
    from hub.persistence.models import IdempotencyRecord

    with get_session() as s:
        n = s.execute(delete(IdempotencyRecord)).rowcount
        s.commit()
    return n or 0


def main() -> None:
    LIMITER.reset()
    cleared = clear_idempotency()
    print(f"已清空 {cleared} 条幂等记录（测试需要可重复）")
    proposal_id = make_run_and_proposal()
    print(f"测试用提案：{proposal_id}\n")

    # 准备：用**最近一次聚类**的簇（旧簇可能是 cluster_member 表存在之前跑的，没有成员）
    from hub.persistence.repo_cluster import ClusterRepo
    clusters = ClusterRepo().fetch_latest_clusters()
    if not clusters:
        print("❌ 没有聚类结果，先跑 hub cluster")
        return
    cluster = clusters[0]                      # 已按 review_count 降序
    with get_session() as s:
        members = s.scalars(
            select(ClusterMember).where(ClusterMember.cluster_id == cluster.id)
            .limit(3)).all()
        if not members:
            print(f"❌ 簇 {cluster.id} 没有成员，先跑 hub cluster")
            return
        # 一个**在簇里**的商品（L2 正向测试用）
        in_spu = s.scalars(select(SpuCatalog)
                           .where(SpuCatalog.canonical_spu_id.in_(cluster.spu_ids or []))
                           .limit(1)).first()
        # 一个**不在簇里**的商品（依据不匹配的负向测试用）
        out_spu = s.scalars(select(SpuCatalog)
                            .where(SpuCatalog.canonical_spu_id.notin_(
                                cluster.spu_ids or []))
                            .limit(1)).first()
    if in_spu is None or out_spu is None:
        print("❌ 找不到在簇里/不在簇里的商品")
        return
    spu_id = in_spu.canonical_spu_id
    cluster_id = str(cluster.id)
    print(f"簇={cluster_id[:8]}（{cluster.review_count} 条，覆盖 {len(cluster.spu_ids)} 个商品）")
    print(f"在簇里的商品={spu_id}  不在簇里的商品={out_spu.canonical_spu_id}  "
          f"成员={len(members)} 条\n")

    ctx_sys = CallContext(role="system", trace_id="gw-test")
    ctx_op = CallContext(role="operator", trace_id="gw-test")
    ctx_mgr = CallContext(role="manager", trace_id="gw-test")

    def call(tool, args, refs=None, ctx=ctx_mgr, pid=None):
        return invoke(ToolCall(tool_name=tool, args=args,
                               evidence_refs=refs or {},
                               proposal_id=pid or proposal_id), ctx)

    # ---------- ① ~ ⑥ 四道防线（被拒） ----------
    print("=== ①~⑥ 被拒场景 ===")
    r = call("no.such_tool", {})
    check("① 未知工具", r.decision == Decision.REJECTED
          and r.deny_code == DenyCode.TOOL_NOT_FOUND, r.deny_code or "")

    r = call("catalog.query_price",
             {"platform": "taobao", "spu_id": spu_id, "偷偷加的字段": 1})
    check("② 多余字段被拒（extra=forbid）",
          r.deny_code == DenyCode.PARAM_INVALID, r.deny_code or "")

    r = call("listing.update_desc",
             {"platform": "taobao", "spu_id": spu_id, "description": "测试描述内容"},
             refs={"issue_cluster": cluster_id}, ctx=ctx_sys)
    check("③ L2 用 system 角色被拒（没有 system 豁免）",
          r.deny_code == DenyCode.SCOPE_DENIED, r.deny_code or "")

    r = call("listing.update_desc",
             {"platform": "taobao", "spu_id": spu_id, "description": "测试描述内容"},
             refs={})
    check("④ 依据缺失", r.deny_code == DenyCode.EVIDENCE_MISSING, r.deny_code or "")

    r = call("listing.update_desc",
             {"platform": "taobao", "spu_id": spu_id, "description": "测试描述内容"},
             refs={"issue_cluster": str(uuid.uuid4())})
    check("⑤ 依据不存在", r.deny_code == DenyCode.EVIDENCE_NOT_FOUND, r.deny_code or "")

    r = call("listing.update_desc",
             {"platform": "taobao", "spu_id": out_spu.canonical_spu_id,
              "description": "测试描述内容"},
             refs={"issue_cluster": cluster_id})
    check("⑥ 拿 A 簇的证据改 B 商品", r.deny_code == DenyCode.EVIDENCE_MISMATCH,
          r.deny_code or "")

    # ---------- ⑦ L0 执行 ----------
    print("\n=== ⑦ L0 执行 + 回查 ===")
    args_l0 = {"platform": "taobao", "spu_id": spu_id}
    r = call("catalog.query_price", args_l0)
    check("L0 执行", r.decision == Decision.EXECUTED, r.brief())
    check("L0 回查通过", r.verify_ok is True)

    # ---------- ⑨ 幂等 ----------
    print("\n=== ⑨ 幂等 ===")
    r2 = call("catalog.query_price", args_l0)
    check("第二次调用命中幂等（不重复执行）",
          r2.decision == Decision.REPLAYED, r2.brief())
    check("返回的是上次的结果",
          (r2.response or {}).get("price") == (r.response or {}).get("price"))
    check("两次的 execution_id 相同（没有新建执行记录）",
          r2.execution_id == r.execution_id)

    # ---------- ⑧ L1 执行 ----------
    print("\n=== ⑧ L1 执行 + 回查 ===")
    r = call("review.draft_reply", {
        "cluster_id": cluster_id,
        "review_ids": [str(m.record_id) for m in members],
        "content": "【网关测试】您反馈的问题我们已经记录，会安排专人跟进处理。",
        "compliance_flags": [],
    }, refs={"issue_cluster": cluster_id})
    check("L1 执行", r.decision == Decision.EXECUTED, r.brief())
    check("L1 回查通过", r.verify_ok is True)

    # ---------- ⑩ L2 转审批（且没执行） ----------
    print("\n=== ⑩ L2 转审批 ===")
    with get_session() as s:
        before = s.scalars(select(SpuCatalog)
                           .where(SpuCatalog.canonical_spu_id == spu_id)).first()
        desc_before = before.description
    r = call("listing.update_desc",
             {"platform": "taobao", "spu_id": spu_id,
              "description": "【网关测试】这段描述不应该被写进去，因为 L2 要审批。"},
             refs={"issue_cluster": cluster_id}, ctx=ctx_op)
    check("L2 → PENDING_APPROVAL",
          r.decision == Decision.PENDING_APPROVAL, r.brief())
    with get_session() as s:
        after = s.scalars(select(SpuCatalog)
                          .where(SpuCatalog.canonical_spu_id == spu_id)).first()
        unchanged = after.description == desc_before
    check("L2 真的没有改数据", unchanged)

    # ---------- ⑪ L3 转人工（且没执行） ----------
    print("\n=== ⑪ L3 转人工 ===")
    # 找一个价格异常当依据
    from hub.persistence.models import Anomaly
    with get_session() as s:
        anomaly = s.scalars(select(Anomaly).where(Anomaly.type == "price")
                            .limit(1)).first()
    if anomaly is None:
        check("有价格异常可用", False, "先跑 hub price")
    else:
        # 异常指向的是 canonical id，要反查平台商品
        from hub.persistence.models import SkuMapping
        with get_session() as s:
            m = s.scalars(select(SkuMapping)
                          .where(SkuMapping.canonical_spu_id == anomaly.canonical_spu_id)
                          .limit(1)).first()
        price_before = None
        if m:
            with get_session() as s:
                row = s.scalars(select(SpuCatalog)
                                .where(SpuCatalog.canonical_spu_id == m.platform_spu_id)
                                .where(SpuCatalog.platform == m.platform)).first()
                price_before = str(row.price_ref) if row else None
            r = call("product.update_price",
                     {"platform": m.platform, "spu_id": m.platform_spu_id,
                      "new_price": "1.00"},
                     refs={"anomaly": str(anomaly.id)}, ctx=ctx_mgr)
            check("L3 → PENDING_HITL", r.decision == Decision.PENDING_HITL, r.brief())
            with get_session() as s:
                row = s.scalars(select(SpuCatalog)
                                .where(SpuCatalog.canonical_spu_id == m.platform_spu_id)
                                .where(SpuCatalog.platform == m.platform)).first()
                check("L3 真的没有改价格", str(row.price_ref) == price_before,
                      f"仍为 {row.price_ref}")

    # ---------- ⑫ 频控 ----------
    print("\n=== ⑫ 频控 ===")
    LIMITER.reset()
    with get_session() as s:
        all_spus = s.scalars(select(SpuCatalog.canonical_spu_id)
                             .where(SpuCatalog.platform == "taobao").limit(14)).all()
    limited = False
    for sid in all_spus:
        rr = call("catalog.query_price", {"platform": "taobao", "spu_id": sid})
        if rr.deny_code == DenyCode.RATE_LIMITED:
            limited = True
            break
    check(f"连打 {len(all_spus)} 次后触发限流（burst=10）", limited)

    # ---------- 汇总 ----------
    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ 检查点 B 通过：四道防线 + 风险分级都按预期工作")
    print("\n注意：L2/L3 只返回「待审批/待人工」，没有改任何数据。")


if __name__ == "__main__":
    main()
