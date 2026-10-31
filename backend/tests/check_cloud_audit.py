# -*- coding: utf-8 -*-
"""
补 §5.2 验收④ 和 §5.3 指标③（check_cloud_audit.py）
====================================================
补两件路线图明确要求、我第一版漏掉的事：

    [1] **每次出云写 audit_log**（§5.2 验收④）
        · 云端 + 聚合 → 写一条 `cloud.request`
        · 云端 + 原文 + 脱敏 → 写一条，detail 里记"脱敏几处"
        · **本地请求不写**（没出网，不产生审计噪音 ✅）

    [2] **每步 token**（§5.3 指标③）
        · `handle()` 跑完后 `tokens_in/out` 不再恒为 0
        · judge 那步的 `args` 里有本次用量（便于"每步"看 ✅）

⚠️ [1] 会**真的写 audit_log**，测试自己清理（按 action='cloud.request' + 时间窗口）
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select                      # noqa: E402

from hub.core.cloud_guard import (KIND_AGGREGATE, KIND_TEXT,  # noqa: E402
                                  CloudBlocked, guard)
from hub.persistence.db import get_session                 # noqa: E402
from hub.persistence.models import AuditLog, IssueCluster   # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def count_cloud_audit(since: datetime) -> int:
    with get_session() as s:
        return len(s.scalars(select(AuditLog).where(
            AuditLog.action == "cloud.request",
            AuditLog.created_at >= since,
        )).all())


def cleanup(since: datetime) -> None:
    with get_session() as s:
        s.execute(delete(AuditLog).where(
            AuditLog.action == "cloud.request", AuditLog.created_at >= since))
        s.commit()


def main() -> int:
    print("=" * 64)
    print("  出云审计 + 每步 token")
    print("=" * 64)
    t0 = datetime.now(timezone.utc) - timedelta(seconds=2)

    # ---------- [1] 出云写审计 ----------
    print("\n[1] 每次出云写 audit_log（§5.2 验收④）")
    before = count_cloud_audit(t0)
    guard("https://api.example.com/v1", KIND_AGGREGATE, "GMV 123 万 —— 全部正常",
          allow_aggregate=True)
    n1 = count_cloud_audit(t0)
    check("聚合出云 → 写了 1 条", n1 - before == 1, f"{before} → {n1}")

    # 带手机号的原文 → 脱敏后放行 → 也要写，并记脱敏处数
    raw = "买家 13812345678 说掉色严重，地址在上海市浦东新区XX路88号"
    res = guard("https://api.example.com/v1", KIND_TEXT, raw,
                allow_text=True, do_redact=True)
    n2 = count_cloud_audit(t0)
    check("原文出云（脱敏后）→ 又写了 1 条", n2 - n1 == 1, f"{n1} → {n2}")
    check("脱敏真的发生了", len(res.redactions) > 0, f"{len(res.redactions)} 处")
    with get_session() as s:
        row = s.scalars(select(AuditLog).where(
            AuditLog.action == "cloud.request",
            AuditLog.created_at >= t0,
        ).order_by(AuditLog.id.desc())).first()
        d = row.detail if row else {}
    check("detail 里有 chars", "chars" in d, str(d)[:80])
    check("detail 里记了脱敏处数", d.get("redactions") == len(res.redactions),
          f"detail={d.get('redactions')} / 实际={len(res.redactions)}")
    check("actor 是 system:cloud_guard（阶段 4 再换真人）",
          (row.actor if row else "") == "system:cloud_guard", str(row.actor if row else ""))

    # 本地请求不写审计（不然全是噪音 ✅）
    n3 = count_cloud_audit(t0)
    guard("http://localhost:11434/v1", KIND_TEXT, "本地请求", allow_text=False)
    check("**本地请求不写审计**", count_cloud_audit(t0) == n3, "没有新增")

    # 被拒的请求不写 audit（它没出网 ✅）—— 而且应该抛
    try:
        guard("https://api.example.com/v1", KIND_TEXT, "原文", allow_text=False)
        check("allow_text=false 时应抛 CloudBlocked", False)
    except CloudBlocked:
        check("allow_text=false 时抛 CloudBlocked", True)
    check("被拒的请求**不写** audit（没出网）", count_cloud_audit(t0) == n3)

    cleanup(t0)

    # ---------- [2] 每步 token ----------
    print("\n[2] 每步 token（§5.3 指标③）")
    with get_session() as s:
        c = s.scalars(select(IssueCluster).limit(1)).first()
    if c is None:
        print("  [!] 库里没有簇，跳过")
    else:
        from hub.agent.handle import handle

        class TokLLM:
            """假模型：返回合法 JSON，并报 token 用量（模拟 API 的 usage 字段）。"""
            name = "fake"

            def __init__(self):
                self.last_usage = (0, 0)
                self.calls = 0

            def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None):
                self.calls += 1
                self.last_usage = (123, 45)      # 每次固定报这个数
                return json.dumps({"tool": "review.draft_reply", "evidence_ids": ["E1"],
                                   "recommendation": "回复差评", "confidence": 0.8},
                                  ensure_ascii=False)

        llm = TokLLM()
        from hub.agent import guard as g
        r = handle(str(c.id), llm=llm, limits=g.AgentLimits(max_concurrent=99))
        check("tokens_in 不再恒为 0", r.tokens_in > 0, str(r.tokens_in))
        check("tokens_out 不再恒为 0", r.tokens_out > 0, str(r.tokens_out))
        check("等于调用次数 × 每次用量",
              r.tokens_in == 123 * llm.calls and r.tokens_out == 45 * llm.calls,
              f"in={r.tokens_in} out={r.tokens_out} calls={llm.calls}")
        judge_steps = [s for s in r.steps if s.tool == "llm.judge"]
        check("judge 步的 args 里有本次用量",
              bool(judge_steps) and "tokens_in" in (judge_steps[-1].args or {}),
              str((judge_steps[-1].args if judge_steps else {}))[:80])

    print("\n" + "=" * 64)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 64)
        return 1
    print("  全部通过")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
