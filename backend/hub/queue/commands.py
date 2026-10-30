# -*- coding: utf-8 -*-
"""
M7 的四个命令（commands.py）
===========================
    hub enqueue [--kind full]       把一条链塞进队列（会做防重）
    hub worker [--once] [--idle N]  常驻消费
    hub queue [--dlq]               看队列状态 / 死信列表
    hub dlq replay --id <run>       重放死信

命令实现放这里而不是 `cli.py`：`cli.py` 已经 500+ 行，再塞会看不清。
"""
import uuid

from hub.persistence.db import get_session
from hub.persistence.models import PipelineRun
from hub.queue import client as q
from hub.queue.stages import STAGES, total_est_s


# ==================== enqueue ====================

def enqueue(kind: str = "full", params: dict | None = None) -> int:
    """入队一条链。返回退出码（0 成功 / 1 已有在跑的）。

    **防重靠数据库部分唯一索引**，不靠"先查再插"——
    两个进程同时插时，"先查"都通过，"再插"就两条了。
    这里故意让 IntegrityError 冒出来再翻译成人话。

    **为什么目前只允许 kind="full"**：
        部分唯一索引是"同 kind 只能一条"，所以 `full` 和 `backfill` 能同时存在。
        而两条链都会跑 ingest/dqe/cluster——**同时写同一批记录，数据会打架**
        （GPU 锁只能防抢显存，防不了两个进程同时 upsert 同一张表）。
        所以现在把 kind 锁死；等真要做回填时，改成"全局只允许一条链"的索引
        （`UNIQUE ((true)) WHERE status IN ('QUEUED','RUNNING')`）。
    """
    from sqlalchemy.exc import IntegrityError

    trace_id = uuid.uuid4().hex
    try:
        with get_session() as s:
            row = PipelineRun(trace_id=trace_id, kind=kind, status="QUEUED",
                              params=params or {})
            s.add(row)
            s.flush()
            run_id = str(row.id)
            s.commit()
    except IntegrityError:
        print(f"❌ 已经有一条 `{kind}` 类型的链在排队/运行中。")
        print("   看：hub queue")
        print("   等它跑完，或先 `hub worker` 把它消化掉。")
        return 1

    r = q.get_redis()
    ok, msg = q.ping(r)
    if not ok:
        print(f"❌ 连不上 Redis：{msg}")
        print("   链已写进数据库（status=QUEUED），但没进队列。Redis 修好后")
        print("   跑一次 `hub worker` 会被对账器捡起来，不会丢。")
        return 2
    q.ensure_group(r)
    msg_id = q.enqueue(r, run_id)

    # 清理：只保留最近 50 条【已结束】的链，避免 pipeline_run 无限增长
    # （每天一条 + 回填，一年就是几百条；旧的不删，`hub queue` 的列表会越来越长）
    _prune_old_runs(keep=50)

    print(f"✅ 已入队")
    print(f"   链 ID    {run_id}")
    print(f"   trace    {trace_id}")
    print(f"   消息 ID  {msg_id}")
    print(f"   预计耗时 {total_est_s()} 秒（{len(STAGES)} 个阶段）")
    print("\n   跑：hub worker")
    return 0


def _prune_old_runs(keep: int = 50) -> int:
    """删掉太旧的**已结束**链（DONE/DEGRADED/DEAD/FAILED）。

    **绝不删 QUEUED/RUNNING**——那是"还没干完的活"，
    删了就等于静默丢任务。所以过滤条件里必须带上状态。
    """
    from sqlalchemy import select

    with get_session() as s:
        finished = s.scalars(
            select(PipelineRun)
            .where(PipelineRun.status.in_(["DONE", "DEGRADED", "DEAD", "FAILED"]))
            .order_by(PipelineRun.created_at.desc())
        ).all()
        old = finished[keep:]
        for row in old:
            s.delete(row)
        s.commit()
    return len(old)


# ==================== worker ====================

def worker(once: bool = False, idle: int | None = None) -> int:
    """worker 命令入口。

    **为什么最后用 `os._exit` 而不是 `return`**：
        实测：`--once` 跑完（KMeans/OpenMP 用了多线程之后）进程不退出，
        卡在解释器关停阶段（Windows 上 OpenMP 线程会拖住 shutdown）。
        先 flush 再强制退出——不然最后几行 print 还留在缓冲区里看不见。

    **安全性**：退出前所有数据库会话都已 commit + close，
    Redis 连接随进程消失即可，不需要优雅关闭。
    """
    import os
    import sys

    from hub.queue.worker import run

    code = run(once=once, idle_s=idle)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# ==================== queue 观测 ====================

def show_queue(dlq: bool = False) -> int:
    """看队列状态。`--dlq` 时列出死信明细。"""
    r = q.get_redis()
    ok, msg = q.ping(r)
    if not ok:
        print(f"❌ 连不上 Redis：{msg}")
        return 2

    if dlq:
        return _show_dlq(r)

    st = q.queue_stats(r)
    print(f"\n{'=' * 56}")
    print("  队列状态")
    print(f"{'=' * 56}")
    print(f"  队列深度（未消费）   {st['depth']}")
    print(f"  PEL（已领未确认）    {st['pending']}   ← 有值说明有 worker 在跑或崩了")
    print(f"  延迟队列（待重试）   {st['delayed']}")
    print(f"  死信                 {st['dlq']}")

    with get_session() as s:
        from sqlalchemy import select
        rows = s.scalars(select(PipelineRun)
                         .order_by(PipelineRun.created_at.desc()).limit(10)).all()
    if rows:
        print(f"\n  最近 10 条链：")
        print(f"  {'状态':<10}{'断点':<12}{'次数':<6}{'降级':<6}{'链 ID':<10}")
        for row in rows:
            print(f"  {row.status:<10}{(row.current_stage or '-'):<12}"
                  f"{row.attempt:<6}{'是' if row.degraded else '否':<6}{str(row.id)[:8]}")
    print()
    return 0


def _show_dlq(r) -> int:
    from hub.core.config import Settings
    _q = Settings().queue
    try:
        entries = r.xrange(_q.dlq_key, count=50)
    except Exception:                            # noqa: BLE001
        entries = []
    if not entries:
        print("死信队列是空的 ✅")
        return 0
    print(f"\n死信 {len(entries)} 条（最近 50）：\n")
    for msg_id, fields in entries:
        print(f"  {msg_id}  run={fields.get('run_id', '')[:8]}  "
              f"{fields.get('failed_at', '')[:19]}")
        print(f"      错误：{fields.get('error', '')[:120]}")
    print("\n重放：hub dlq replay --id <run_id 前 8 位>")
    return 0


# ==================== dlq replay ====================

def replay(run_id_prefix: str) -> int:
    """重放死信：把链状态改回 QUEUED、attempt 归零、重新入队。

    **断点不清零**（current_stage 保留）——否则重放会从头跑 450 秒。
    """
    from sqlalchemy import select

    with get_session() as s:
        rows = s.scalars(select(PipelineRun)
                         .where(PipelineRun.status == "DEAD")).all()
        hit = [x for x in rows if str(x.id).startswith(run_id_prefix)]
        if not hit:
            print(f"❌ 没找到状态为 DEAD 且 ID 以 {run_id_prefix} 开头的链")
            return 1
        row = hit[0]
        row.status = "QUEUED"
        row.attempt = 0
        row.error = None
        run_id = str(row.id)
        stage = row.current_stage
        s.commit()

    r = q.get_redis()
    q.ensure_group(r)
    q.enqueue(r, run_id)
    print(f"✅ 已重放 {run_id[:8]}（从断点 {stage} 之后继续）")
    print("   跑：hub worker")
    return 0
