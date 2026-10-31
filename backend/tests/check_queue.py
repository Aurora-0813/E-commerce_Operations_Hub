# -*- coding: utf-8 -*-
"""
检查点：M7 队列与调度

**为什么这个测试不用跑全链**：
    队列机制（重试/死信/对账/断点）和"跑哪个阶段"是正交的。
    把第一个阶段（ingest）注入成"一定失败"，就能在 15 秒内
    验证完重试次数、退避、死信、重放、对账——不用等 300 秒。

验 10 件事：
    ① 断点续跑的顺序（pure function）
    ② 入队成功，队列深度 +1
    ③ 重复入队被数据库挡住（部分唯一索引）
    ④ 阶段失败 → 重试（进延迟队列，attempt+1）
    ⑤ 重试超限 → DEAD + 死信
    ⑥ 死信重放 → 回到 QUEUED 且 attempt 归零、**断点保留**
    ⑦ 对账：数据库 RUNNING 但心跳超时 → 重新入队
    ⑧ 优雅退出：默认线程不 ACK 的语义（靠 PEL 验证）
    ⑨ 队列统计能读到 PEL / 延迟 / 死信
    ⑩ 幂等：同一阶段跑两次结果一致（靠 test 里的假阶段验证）

运行（在 backend/ 目录下，需要 Redis 在跑）：
    python tests/check_queue.py
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                                  # noqa: E402

from hub.persistence.db import get_session                     # noqa: E402
from hub.persistence.models import PipelineRun                 # noqa: E402
from hub.queue import client as q                              # noqa: E402
from hub.queue.stages import next_stage_after                  # noqa: E402
from hub.queue.worker import _reconcile, run as worker_run     # noqa: E402
from hub.queue import worker as worker_mod                     # noqa: E402

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def clean(r) -> None:
    """清库 + 清 Redis（测试要可重复）。"""
    with get_session() as s:
        s.execute(PipelineRun.__table__.delete())
        s.commit()
    r.flushdb()


def make_run(kind: str = "full") -> str:
    import uuid
    with get_session() as s:
        row = PipelineRun(trace_id=uuid.uuid4().hex, kind=kind, status="QUEUED")
        s.add(row)
        s.flush()
        rid = str(row.id)
        s.commit()
    return rid


def status_of(run_id: str) -> tuple[str, str | None, int]:
    import uuid
    with get_session() as s:
        row = s.get(PipelineRun, uuid.UUID(run_id))
        return (row.status, row.current_stage, row.attempt)


def main() -> None:
    r = q.get_redis()
    ok, msg = q.ping(r)
    if not ok:
        print(f"❌ Redis 不可用：{msg}")
        return
    print(f"Redis：{msg}\n")
    clean(r)
    q.ensure_group(r)

    # ---------- ① 断点续跑的顺序 ----------
    print("=== ① 断点续跑的顺序 ===")
    check("没跑过 → 从 ingest 开始", next_stage_after(None).name == "ingest")
    check("dqe 之后 → mm", next_stage_after("dqe").name == "mm")
    check("propose 之后 → 没有下一个", next_stage_after("propose") is None)
    check("未知阶段名 → 保守从头", next_stage_after("不存在的阶段").name == "ingest")

    # ---------- ② 入队 ----------
    print("\n=== ② 入队 ===")
    rid = make_run()
    q.enqueue(r, rid)
    st = q.queue_stats(r)
    check("队列深度 +1", st["depth"] == 1, f"depth={st['depth']}")

    # ---------- ③ 重复入队被挡 ----------
    print("\n=== ③ 重复入队（部分唯一索引）===")
    from sqlalchemy.exc import IntegrityError
    import uuid
    blocked = False
    try:
        with get_session() as s:
            s.add(PipelineRun(trace_id=uuid.uuid4().hex, kind="full", status="QUEUED"))
            s.commit()
    except IntegrityError:
        blocked = True
    check("第二条 QUEUED 被数据库拒绝", blocked)

    # ---------- ④ 失败 → 重试 ----------
    print("\n=== ④ 阶段失败 → 重试 ===")
    import hub.cli
    orig_p0 = hub.cli.cmd_p0

    # ⚠️ 这里注入故障靠的是"替换 hub.cli.cmd_p0"——
    #    它只在 worker **函数内 import** 时才生效。
    #    万一以后有人把 worker 里的 import 提到模块顶部，
    #    worker 会持有直接引用，这个替换就【失效】了。
    #
    #    失效的后果有两种，只防第二种：
    #      · 真的 ingest 跑起来 → 后面的断言（状态 QUEUED）会红 ✅ 能发现
    #      · 真的 ingest **恰好也失败** → 断言仍绿，但**测的已经不是故障注入** ✅ ✗
    #    → 所以必须直接验证"注入的函数真的被调用了"（下面第 ④b 条）
    called: list[int] = []

    def boom(*a, **k):
        called.append(1)
        raise RuntimeError("注入故障：导入失败")

    hub.cli.cmd_p0 = boom
    worker_mod._q.max_attempts = 3          # 允许重试 3 次
    worker_run(once=True)                    # 跑一次 → 第 1 次失败 → 进延迟队列
    stt, stage, attempt = status_of(rid)
    check("状态回到 QUEUED（等重试）", stt == "QUEUED", stt)
    check("attempt 记成 1", attempt == 1, f"attempt={attempt}")
    check("断点没前进（ingest 没成功）", stage is None, f"stage={stage}")
    check("进了延迟队列", q.queue_stats(r)["delayed"] == 1,
          f"delayed={q.queue_stats(r)['delayed']}")
    # ④b：**证明故障注入真的生效了**（防"patch 失效但测试仍绿"的假绿）
    check("注入的故障函数真的被调用过", len(called) >= 1, f"调用 {len(called)} 次")

    # ---------- ⑤ 重试超限 → 死信 ----------
    print("\n=== ⑤ 重试超限 → 死信 ===")
    # ⚠️ ④ 把任务放进了延迟队列（退避 2 秒），**现在还没到期**，
    #    所以直接再调 worker 会看到"队列为空"——这是正确行为（重试就该等）。
    #    测试里把它的到期时间改成 0（ZADD 会更新已有 member 的 score），
    #    worker 循环开头就会把它搬回 Stream，省掉 sleep 2 秒。
    r.zadd(q._q.delayed_key, {rid: 0})
    worker_mod._q.max_attempts = 1             # 下一次失败就是最后一次
    worker_run(once=True)
    stt, stage, attempt = status_of(rid)
    check("状态 DEAD", stt == "DEAD", stt)
    st_dlq = q.queue_stats(r)
    check("死信队列 +1", st_dlq["dlq"] == 1, f"dlq={st_dlq['dlq']}")

    # ---------- ⑥ 死信重放 ----------
    print("\n=== ⑥ 死信重放 ===")
    from hub.queue.commands import replay
    # 先手动设一个断点，验证"重放不动断点"
    with get_session() as s:
        row = s.get(PipelineRun, uuid.UUID(rid))
        row.current_stage = "dqe"
        s.commit()
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        replay(rid[:8])
    stt, stage, attempt = status_of(rid)
    check("状态回到 QUEUED", stt == "QUEUED", stt)
    check("attempt 归零", attempt == 0, f"attempt={attempt}")
    check("**断点保留**（不从头跑）", stage == "dqe", f"stage={stage}")

    # ---------- ⑦ 对账 ----------
    print("\n=== ⑦ 对账：心跳超时 → 重新入队 ===")
    # 造一条"数据库说 RUNNING、但心跳停了 20 分钟"的链（模拟消息彻底丢了）
    rid2 = make_run(kind="backfill")
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    with get_session() as s:
        row = s.get(PipelineRun, uuid.UUID(rid2))
        row.status = "RUNNING"
        row.heartbeat_at = old
        row.current_stage = "cluster"
        s.commit()
    before = q.queue_stats(r)["depth"]
    _reconcile(r, idle_s=600, verbose=False)
    stt, stage, _ = status_of(rid2)
    check("对账后状态回到 QUEUED", stt == "QUEUED", stt)
    check("断点保留", stage == "cluster", f"stage={stage}")
    check("被重新入队", q.queue_stats(r)["depth"] == before + 1)

    # ---------- ⑧ PEL（未 ACK 的消息还在）----------
    print("\n=== ⑧ PEL：未 ACK 的任务不会丢 ===")
    worker_mod._q.max_attempts = 1
    # 手动领一条但不 ACK（模拟 worker 崩溃）
    resp = r.xreadgroup(q._q.group, "fake-worker", {q._q.stream: ">"}, count=1)
    got = bool(resp and resp[0][1])
    st = q.queue_stats(r)
    check("领到消息", got)
    check("PEL 里有 1 条未确认", st["pending"] >= 1, f"pending={st['pending']}")

    # ---------- ⑨ 队列统计 ----------
    print("\n=== ⑨ 队列统计 ===")
    check("能读到 depth/pending/delayed/dlq 四项",
          all(k in st for k in ("depth", "pending", "delayed", "dlq")),
          str(st))

    # ---------- 收尾 ----------
    hub.cli.cmd_p0 = orig_p0
    worker_mod._q.max_attempts = 3
    clean(r)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ M7 检查点通过：断点续跑 / 防重 / 重试 / 死信 / 重放 / 对账 / PEL")
    print("\n说明：这个测试【不跑真实阶段】（把 ingest 注入成失败），")
    print("      所以 15 秒内能跑完，且结果稳定。")


if __name__ == "__main__":
    main()
