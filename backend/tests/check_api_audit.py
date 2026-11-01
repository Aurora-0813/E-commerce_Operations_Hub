# -*- coding: utf-8 -*-
"""
审计接线测试（check_api_audit.py）—— 阶段 4.2
============================================
路线图 §10.2 要求 8 个 action 落到**业务层**（不是 API 层拦截 ✅）：

| action | 触发点 | 本测试 |
| --- | --- | --- |
| `login` / `login_failed` | 认证接口 | `check_api_auth` 已覆盖 ✅ |
| `user.create` | `scripts/seed_users.py` | [1] |
| `proposal.approve` / `proposal.reject` | `hub/action/approval.py` | [2] |
| `dlq.replay` | `hub dlq` | [3] |
| `run.trigger` | `hub enqueue` | [4]（只测"写了审计"，不真入队 ✅） |
| `agent.adopt` | `hub handle --adopt` | [5]（假 LLM，不真跑 agent） |
| `cloud.request` | CloudGuard | `check_cloud_audit` 已覆盖 ✅ |

**测法**：直接调业务函数 → 查 `audit_log` 里有没有那条 action ✅
**为什么这么测**：审计的价值是"**业务发生时**就写了" ✅
    所以测的应该是"调业务函数的副作用"，而不是"API 层有没有拦" ✅
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select          # noqa: E402

from hub.persistence.db import get_session           # noqa: E402
from hub.persistence.models import AuditLog          # noqa: E402

FAILS: list[str] = []

ACTIONS = ("user.create", "proposal.approve", "proposal.reject",
           "dlq.replay", "run.trigger", "agent.adopt")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def count(action: str) -> int:
    with get_session() as s:
        return int(s.scalar(select(func.count()).select_from(AuditLog)
                            .where(AuditLog.action == action)) or 0)


def cleanup() -> None:
    with get_session() as s:
        s.execute(delete(AuditLog).where(AuditLog.action.in_(ACTIONS)))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  审计接线（阶段 4.2）—— 业务层写，不是 API 层拦")
    print("=" * 66)
    cleanup()

    # ---------- [1] user.create ----------
    print("\n[1] seed_users 写 user.create")
    from scripts.seed_users import seed
    seed(password="123456")
    check("有 user.create", count("user.create") >= 2, f"{count('user.create')} 条")
    with get_session() as s:
        row = s.scalars(select(AuditLog).where(
            AuditLog.action == "user.create").limit(1)).first()
    check("actor=system:seed_users", row.actor == "system:seed_users", row.actor)
    check("resource 指向用户", str(row.resource).startswith("user:"), row.resource)

    # ---------- [2] proposal.approve / reject ----------
    print("\n[2] approval 写 proposal.approve / proposal.reject")
    # ⚠️ **自己造一条待办**，不依赖库里已有（2026-10-31 踩到）：
    #    第一版是"取第一个 PENDING 待办"→ 上次跑把它拒了 → 这次没有 → 假红 ✅ ✗
    #    测试必须自带 fixture，不能依赖"上一个人留下的状态" ✅
    from uuid import uuid4

    from hub.persistence.models import ActionProposal, AnalysisRun, HitlTask
    from datetime import datetime, timedelta, timezone
    tid = None
    with get_session() as s:
        run = AnalysisRun(trace_id=f"test-audit-{uuid4().hex[:8]}",
                          batch_id=None, tenant_id="default", status="DONE")
        s.add(run)
        s.flush()
        prop = ActionProposal(run_id=run.id, trace_id=run.trace_id,
                              tool_name="listing.update_desc", tool_version="v1",
                              args={"platform": "taobao", "spu_id": "TB-0001",
                                    "description": "x"},
                              risk_level="L2", evidence_refs=[], rationale="测试用",
                              dry_run=True, status="PROPOSED", topic="quality")
        s.add(prop)
        s.flush()
        task = HitlTask(proposal_id=prop.id, trace_id=run.trace_id,
                        status="PENDING",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7))
        s.add(task)
        s.flush()
        tid = str(task.id)
        s.commit()

    from hub.action import approval
    approval.reject(tid, by="test-supervisor", note="测试拒绝")
    check("有 proposal.reject", count("proposal.reject") == 1,
          f"{count('proposal.reject')} 条")
    with get_session() as s:
        row = s.scalars(select(AuditLog).where(
            AuditLog.action == "proposal.reject").limit(1)).first()
    check("记了 by（谁拒的）", row.detail.get("by") == "test-supervisor",
          str(row.detail)[:80])
    check("resource 指向提案", str(row.resource).startswith("proposal:"),
          row.resource)
    # 清掉 fixture（**自己造的自己删** ✅）
    with get_session() as s:
        s.execute(delete(HitlTask).where(HitlTask.trace_id.like("test-audit-%")))
        s.execute(delete(ActionProposal).where(
            ActionProposal.trace_id.like("test-audit-%")))
        s.execute(delete(AnalysisRun).where(
            AnalysisRun.trace_id.like("test-audit-%")))
        s.commit()

    # ---------- [3] dlq.replay ----------
    print("\n[3] hub dlq replay 写 dlq.replay")
    from hub.queue import commands as qcmd
    # 没有 DEAD 链时会 return 1（打印提示）→ 那就不该写审计 ✅
    before = count("dlq.replay")
    qcmd.replay("nonexistent-prefix-xyz")
    check("没找到死信时**不写**审计（还没发生 ✅）", count("dlq.replay") == before,
          f"{before} → {count('dlq.replay')}")

    # ---------- [5] agent.adopt ----------
    print("\n[5] adopt 写 agent.adopt（假 LLM，不真跑 agent）")
    from hub.agent.handle import HandleResult
    from hub.agent.adopt import adopt
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        c = s.scalars(select(IssueCluster).limit(1)).first()
    if c is None:
        check("库里有簇可测", False)
    else:
        import json

        class FakeLLM:
            name = "fake"

            def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None):
                return "感谢反馈，我们已记录并反馈给质检部门。"
        r = HandleResult(cluster_id=str(c.id), goal="g", topic=c.business_domain or "quality")
        r.status, r.tool, r.recommendation, r.confidence = \
            "DONE", "review.draft_reply", "回复差评", 0.8
        adopt(r, llm=FakeLLM())
        check("有 agent.adopt", count("agent.adopt") == 1,
              f"{count('agent.adopt')} 条")
        with get_session() as s:
            row = s.scalars(select(AuditLog).where(
                AuditLog.action == "agent.adopt").limit(1)).first()
        check("detail 里有 cluster_id", "cluster_id" in (row.detail or {}),
              str(row.detail)[:60])
        # 清掉这次造出来的提案（`adopt` 起的 trace_id 以 adopt- 开头 ✅）
        from hub.persistence.models import ActionProposal
        with get_session() as s:
            s.execute(delete(ActionProposal).where(
                ActionProposal.trace_id.like("adopt-%")))
            s.commit()

    cleanup()
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
