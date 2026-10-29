# -*- coding: utf-8 -*-
"""
检查点 C：执行链（重试 / 补偿 / 死信）+ 审批流

验 6 件事：
    ① 失败两次后重试成功（attempts=3，回查通过）
    ② 重试耗尽 → 进死信（不瞎补偿）
    ③ 回查失败 → 补偿回滚（描述真的回到旧值）
    ④ 审批流：PENDING → approve（manager）→ 执行 + 回查
    ⑤ 审批流：角色不够 → 不执行，待办仍 PENDING
    ⑥ 审批流：reject → 什么都不做

运行（在 backend/ 目录下，需要先跑过 M3/M4）：
    python tests/check_execute.py
"""
import sys
import uuid
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import delete, select                      # noqa: E402

import hub.tools                                            # noqa: E402,F401
from hub.action.approval import approve, list_pending, reject   # noqa: E402
from hub.gateway import Decision, DenyCode, ToolCall, invoke    # noqa: E402
from hub.gateway import faults                              # noqa: E402
from hub.persistence.db import get_session                  # noqa: E402
from hub.persistence.models import (                        # noqa: E402
    ActionProposal,
    AnalysisRun,
    ClusterMember,
    DeadLetter,
    HitlTask,
    IdempotencyRecord,
    SpuCatalog,
)
from hub.persistence.repo_cluster import ClusterRepo        # noqa: E402
from hub.tools.spec import CallContext, get as get_spec     # noqa: E402

FAILED = []
PREFIX = "【执行链测试】"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def fresh_state() -> None:
    """清掉幂等/待办/死信，让测试可重复。"""
    with get_session() as s:
        s.execute(delete(IdempotencyRecord))
        s.execute(delete(HitlTask))
        s.execute(delete(DeadLetter))
        s.commit()


def make_proposal(tool: str, args: dict, refs: dict) -> str:
    """建一条提案（tool_execution 和 hitl_task 都要挂它）。"""
    spec = get_spec(tool)
    with get_session() as s:
        run = AnalysisRun(trace_id=f"exec-test-{uuid.uuid4().hex[:8]}",
                          tenant_id="default", status="RUNNING")
        s.add(run)
        s.flush()
        p = ActionProposal(
            run_id=run.id, trace_id=run.trace_id,
            tool_name=tool, tool_version=spec.version,
            args=args, risk_level=spec.risk_level,
            evidence_refs=[refs], rationale="测试提案",
            dry_run=True, status="PROPOSED",
        )
        s.add(p)
        s.flush()
        pid = str(p.id)
        s.commit()
    return pid


def main() -> None:
    fresh_state()

    clusters = ClusterRepo().fetch_latest_clusters()
    if not clusters:
        print("❌ 没有聚类结果，先跑 hub cluster")
        return
    cluster = clusters[0]
    with get_session() as s:
        in_spu = s.scalars(select(SpuCatalog)
                           .where(SpuCatalog.canonical_spu_id.in_(cluster.spu_ids or []))
                           .limit(1)).first()
        member = s.scalars(select(ClusterMember)
                           .where(ClusterMember.cluster_id == cluster.id)
                           .limit(1)).first()
    if in_spu is None or member is None:
        print("❌ 簇里没有可用商品或成员")
        return
    spu_id, platform, cluster_id = in_spu.canonical_spu_id, in_spu.platform, str(cluster.id)
    refs = {"issue_cluster": cluster_id}
    ctx = CallContext(role="manager", trace_id="exec-test")
    print(f"商品={platform}/{spu_id}  簇={cluster_id[:8]}\n")

    def desc_of() -> str | None:
        with get_session() as s:
            row = s.scalars(select(SpuCatalog)
                            .where(SpuCatalog.canonical_spu_id == spu_id)).first()
            return row.description

    def restore_desc(value: str | None) -> None:
        """把描述还原（测试会反复改它，跑完不能留脏数据）。"""
        with get_session() as s:
            row = s.scalars(select(SpuCatalog)
                            .where(SpuCatalog.canonical_spu_id == spu_id)).first()
            row.description = value
            s.commit()

    original_desc = desc_of()          # 跑测试前的原值
    try:
        # ---------- ① 重试成功 ----------
        print("=== ① 失败两次后重试成功 ===")
        faults.configure({"listing.update_desc": ["timeout", "timeout", "ok"]})
        args = {"platform": platform, "spu_id": spu_id,
                "description": f"{PREFIX}第一次：这条描述应该写进去"}
        pid = make_proposal("listing.update_desc", args, refs)
        r = invoke(ToolCall("listing.update_desc", args, refs, pid), ctx, approved=True)
        check("最终执行成功", r.decision == Decision.EXECUTED, r.brief())
        check("用了 3 次尝试", (r.detail or {}).get("attempts") == 3,
              f"attempts={(r.detail or {}).get('attempts')}")
        check("回查通过", r.verify_ok is True)
        check("描述已经是新值", desc_of() == args["description"])

        # ---------- ② 重试耗尽 → 死信 ----------
        print("\n=== ② 重试耗尽 → 死信 ===")
        faults.configure({"listing.update_desc": ["500", "500", "500"]})
        args2 = {"platform": platform, "spu_id": spu_id,
                 "description": f"{PREFIX}第二次：这条不该写进去"}
        pid2 = make_proposal("listing.update_desc", args2, refs)
        with get_session() as s:
            before_dead = len(s.scalars(select(DeadLetter)).all())
        r = invoke(ToolCall("listing.update_desc", args2, refs, pid2), ctx, approved=True)
        check("判定为失败", r.decision == Decision.REJECTED
              and r.deny_code == DenyCode.HANDLER_ERROR, r.deny_code or "")
        with get_session() as s:
            after_dead = len(s.scalars(select(DeadLetter)).all())
        check("进了一条死信", after_dead == before_dead + 1,
              f"{before_dead} → {after_dead}")
        check("没有瞎改数据", desc_of() == args["description"])

        # ---------- ③ 回查失败 → 补偿 ----------
        print("\n=== ③ 回查失败 → 补偿回滚 ===")
        faults.configure(verify_fail={"listing.update_desc"})
        before_desc = desc_of()
        args3 = {"platform": platform, "spu_id": spu_id,
                 "description": f"{PREFIX}第三次：写进去了但回查会失败，应被回滚"}
        pid3 = make_proposal("listing.update_desc", args3, refs)
        r = invoke(ToolCall("listing.update_desc", args3, refs, pid3), ctx, approved=True)
        comp = (r.detail or {}).get("compensate") or {}
        check("回查失败被识别", r.verify_ok is False)
        check("触发了补偿", bool(comp), str(comp)[:80])
        check("补偿成功", comp.get("ok") is True, str(comp)[:80])
        check("描述已回到执行前", desc_of() == before_desc,
              f"现在={str(desc_of())[:30]}")

        # ---------- ④ 审批流：PENDING → approve ----------
        print("\n=== ④ 审批流：PENDING → approve ===")
        faults.configure()                          # 关掉故障注入
        args4 = {"platform": platform, "spu_id": spu_id,
                 "description": f"{PREFIX}第四次：审批通过后才写进去"}
        pid4 = make_proposal("listing.update_desc", args4, refs)
        r = invoke(ToolCall("listing.update_desc", args4, refs, pid4), ctx)
        check("L2 转审批", r.decision == Decision.PENDING_APPROVAL, r.brief())
        task_id = (r.detail or {}).get("hitl_task_id")
        check("建了待办", bool(task_id))
        check("审批前没有改数据", desc_of() != args4["description"])

        rows = list_pending()
        check("hub pending 能看到它",
              any(x["task_id"] == task_id for x in rows), f"共 {len(rows)} 条待办")

        res = approve(task_id, "operator", "同意改描述")
        check("operator 批准后执行成功", res.get("ok") is True,
              str(res.get("decision") or res.get("reason")))
        check("批准后回查通过", res.get("verify_ok") is True)
        check("描述已是新值", desc_of() == args4["description"])

        # ---------- ⑤ 角色不够 ----------
        print("\n=== ⑤ 角色不够 → 不执行 ===")
        args5 = {"platform": platform, "spu_id": spu_id,
                 "description": f"{PREFIX}第五次：operator 批不了改价"}
        # 用 L3 工具测（需要 manager）
        from hub.persistence.models import Anomaly, SkuMapping
        with get_session() as s:
            anomaly = s.scalars(select(Anomaly).where(Anomaly.type == "price").limit(1)).first()
            mapping = None
            if anomaly:
                mapping = s.scalars(select(SkuMapping)
                                    .where(SkuMapping.canonical_spu_id == anomaly.canonical_spu_id)
                                    .limit(1)).first()
        if anomaly and mapping:
            price_before = None
            with get_session() as s:
                row = s.scalars(select(SpuCatalog)
                                .where(SpuCatalog.canonical_spu_id == mapping.platform_spu_id)
                                .where(SpuCatalog.platform == mapping.platform)).first()
                price_before = str(row.price_ref) if row else None
            price_args = {"platform": mapping.platform,
                          "spu_id": mapping.platform_spu_id, "new_price": "1.00"}
            pid5 = make_proposal("product.update_price", price_args,
                                 {"anomaly": str(anomaly.id)})
            r = invoke(ToolCall("product.update_price", price_args,
                                {"anomaly": str(anomaly.id)}, pid5), ctx)
            check("L3 转人工", r.decision == Decision.PENDING_HITL, r.brief())
            task5 = (r.detail or {}).get("hitl_task_id")
            res5 = approve(task5, "operator", "我是运营，我要改价")
            check("operator 批不了 L3", res5.get("ok") is False,
                  str(res5.get("reason"))[:60])
            with get_session() as s:
                row = s.scalars(select(SpuCatalog)
                                .where(SpuCatalog.canonical_spu_id == mapping.platform_spu_id)
                                .where(SpuCatalog.platform == mapping.platform)).first()
                check("价格没变", str(row.price_ref) == price_before, f"仍为 {row.price_ref}")
        else:
            print("  ⏭ 没有价格异常，跳过（先跑 hub price）")

        # ---------- ⑥ reject ----------
        print("\n=== ⑥ reject → 什么都不做 ===")
        args6 = {"platform": platform, "spu_id": spu_id,
                 "description": f"{PREFIX}第六次：这条被拒绝，不该写进去"}
        pid6 = make_proposal("listing.update_desc", args6, refs)
        r = invoke(ToolCall("listing.update_desc", args6, refs, pid6), ctx)
        task6 = (r.detail or {}).get("hitl_task_id")
        res6 = reject(task6, "manager", "不需要改")
        check("拒绝成功", res6.get("ok") is True)
        check("数据没变", desc_of() != args6["description"])

    finally:
        faults.configure()                          # 一定要关
        restore_desc(original_desc)                 # 一定要还原描述
        print(f"\n已还原描述：{str(original_desc)[:40] or '(空)'}")

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ 检查点 C 通过：重试 / 补偿 / 死信 / 审批流 都按预期工作")


if __name__ == "__main__":
    main()
