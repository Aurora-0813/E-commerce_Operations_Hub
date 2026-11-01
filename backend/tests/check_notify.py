# -*- coding: utf-8 -*-
"""
通知 outbox 测试（check_notify.py）—— 阶段 3.7
============================================
路线图 §9.5.1 的验收 4 条：
    ① 事件落库
    ② **重复事件不重复发**（幂等键）
    ③ 合并生效
    ④ 失败的能重试且不无限重试

另外补 3 条（我自己跑的时候发现必须验）：
    ⑤ 没配 webhook → **status=SKIPPED**（不假装 SENT ✅）
    ⑥ 重试到上限 → **status=FAILED**（不无限 ✅）
    ⑦ `scan_and_emit` 能把四类触发源扫出来

全部用假 webhook（一个本地 monkeypatch），**不真发请求** ✅
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select          # noqa: E402

from hub.persistence.db import get_session           # noqa: E402
from hub.persistence.models import Notification      # noqa: E402

FAILS: list[str] = []
TAG = "test-notify-"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cleanup() -> None:
    with get_session() as s:
        s.execute(delete(Notification).where(
            Notification.resource_id.like(TAG + "%")))
        s.commit()


def count(status: str | None = None, res_prefix: str | None = None) -> int:
    with get_session() as s:
        q = select(func.count()).select_from(Notification)
        if status:
            q = q.where(Notification.status == status)
        if res_prefix:
            q = q.where(Notification.resource_id.like(res_prefix + "%"))
        return int(s.scalar(q) or 0)


def main() -> int:
    print("=" * 66)
    print("  通知 outbox（阶段 3.7）")
    print("=" * 66)
    cleanup()

    import hub.notify as N

    # ---------- [1] 事件落库 ----------
    print("\n[1] 事件落库（status=PENDING）")
    nid = N.emit("proposal.pending", TAG + "p1", {"summary": "待批"})
    check("有 id", nid > 0, str(nid))
    check("PENDING 计数 +1", count("PENDING", TAG) == 1, str(count("PENDING", TAG)))

    # ---------- [2] 幂等：同一 resource 不重复 ----------
    print("\n[2] 幂等：同一资源再 emit → 同一个 id，不新增")
    nid2 = N.emit("proposal.pending", TAG + "p1", {"summary": "待批"})
    check("返回同一个 id", nid2 == nid, f"{nid} vs {nid2}")
    check("库里仍只有 1 条", count(None, TAG) == 1, str(count(None, TAG)))

    # ---------- [3] 合并：不同 resource 同 event → 合并成一条 ----------
    print("\n[3] 合并：同 event_type 10 分钟内合并")
    N.emit("proposal.pending", TAG + "p2", {"summary": "待批2"})
    N.emit("proposal.pending", TAG + "p3", {"summary": "待批3"})
    with get_session() as s:
        row = s.scalars(select(Notification).where(
            Notification.status == "PENDING",
            Notification.event_type == "proposal.pending").limit(1)).first()
        items = (row.payload or {}).get("items") or []
    check("只有 1 条通知（合并了）", count("PENDING", TAG) == 1, str(count("PENDING", TAG)))
    check("payload 里累加到 3 个 items", len(items) == 3, f"{len(items)} 个")

    # ---------- [5] 没配 webhook → SKIPPED ----------
    print("\n[5] 没配 webhook → status=SKIPPED（不假装 SENT）")
    N._cfg = lambda: {"webhook_url": "", "webhook_timeout": 5}   # 强制没配
    st = N.dispatch()
    check("dispatch 跑完", isinstance(st, dict), str(st))
    check("**没有 SENT**", count("SENT", TAG) == 0, str(count("SENT", TAG)))
    check("变成 SKIPPED", count("SKIPPED", TAG) >= 1, str(count("SKIPPED", TAG)))
    with get_session() as s:
        row = s.scalars(select(Notification).where(
            Notification.status == "SKIPPED",
            Notification.resource_id.like(TAG + "%")).limit(1)).first()
    check("记了 skip_reason", bool((row.payload or {}).get("skip_reason")),
          str((row.payload or {}).get("skip_reason"))[:50])

    # ---------- [4][6] 假 webhook：先失败两次，第三次成功 ----------
    print("\n[4][6] 失败的能重试，但**不无限重试**")
    cleanup()
    N.emit("dead_letter", TAG + "d1", {"summary": "死信"})
    calls = {"n": 0}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("网络抖动")
        return FakeResp()

    import urllib.request
    _orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    N._cfg = lambda: {"webhook_url": "http://fake.local/hook", "webhook_timeout": 5}
    try:
        s1 = N.dispatch()      # 第 1 次：失败，attempts=1，仍 PENDING
        check("第 1 次失败后仍是 PENDING", count("PENDING", TAG) == 1,
              str(count("PENDING", TAG)))
        s2 = N.dispatch()      # 第 2 次：失败，attempts=2
        check("第 2 次失败后仍 PENDING", count("PENDING", TAG) == 1)
        s3 = N.dispatch()      # 第 3 次：成功
        check("第 3 次成功 → SENT", count("SENT", TAG) == 1, str(count("SENT", TAG)))
        check("一共发了 3 次", calls["n"] == 3, f"calls={calls['n']}")
    finally:
        urllib.request.urlopen = _orig
        N._cfg = lambda: {"webhook_url": "", "webhook_timeout": 5}

    # 无限重试检查：一直失败 → 到 MAX_ATTEMPTS 就 FAILED ✅
    cleanup()
    N.emit("dead_letter", TAG + "d2", {"summary": "死信2"})
    calls2 = {"n": 0}

    def always_fail(req, timeout=None):
        calls2["n"] += 1
        raise RuntimeError("一直失败")

    urllib.request.urlopen = always_fail
    N._cfg = lambda: {"webhook_url": "http://fake.local/hook", "webhook_timeout": 5}
    try:
        for _ in range(5):
            N.dispatch()
        check(f"最多试 {N.MAX_ATTEMPTS} 次", calls2["n"] <= N.MAX_ATTEMPTS,
              f"calls={calls2['n']}")
        # ⚠️ 断言文案里**不能出现 FAILED 这个词**（2026-11-01 踩到）：
        #    `run_tests.py` 的 FAIL_PAT 有 `\bFAILED\b` → 测试自己打印的"FAILED"
        #    会被判成失败标记 ✅ ✗（和"失败 7 次"、"❌ 当测试数据"同一类坑 ✅）
        #    状态值本身还是 FAILED（那是业务状态），只是**文案别说那个词** ✅
        check("最终是终态（不无限重试）", count("FAILED", TAG) == 1,
              str(count("FAILED", TAG)))
    finally:
        urllib.request.urlopen = _orig
        N._cfg = lambda: {"webhook_url": "", "webhook_timeout": 5}

    # ---------- [7] 四类触发源 ----------
    print("\n[7] scan_and_emit 扫四类触发源")
    cleanup()
    # ⚠️ [7] 会从**真实业务表**扫出事件写进通知表（resource_id 不是 TAG 前缀 ✅）
    #    第一版只按 TAG 清 → 残留 → 套件里再跑时 [3] 的计数被干扰 → 假红 ✅ ✗
    #    → 记下 id 区间，扫完按区间清 ✅
    from sqlalchemy import func as _f
    with get_session() as _s:
        max_id_before = int(_s.scalar(select(_f.max(Notification.id))) or 0)
    got = N.scan_and_emit()
    check("返回四个键", set(got) == {"proposal.pending", "retro.due",
                                    "dead_letter", "agent.done"}, str(got))
    check("没有抛异常", True)
    # 清掉这次扫描造出来的（按 id 区间 ✅）
    with get_session() as _s:
        _s.execute(delete(Notification).where(Notification.id > max_id_before))
        _s.commit()

    cleanup()
    print("\n（测试数据已清理 ✅）")
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
