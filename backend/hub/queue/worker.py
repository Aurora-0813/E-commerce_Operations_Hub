# -*- coding: utf-8 -*-
"""
队列消费者（worker.py）—— M7 的心脏
===================================
`hub worker` 是一个常驻进程，做四件事（按频率从高到低）：

    ① 领任务   XREADGROUP 阻塞 5 秒，有活就干
    ② 搬延迟   每 2 秒把到期的重试任务从 ZSET 搬回 Stream
    ③ 抢闲置   每 60 秒 XAUTOCLAIM，把"别的 worker 崩了"的任务抢过来
    ④ 对账     每 60 秒找"数据库里 RUNNING 但心跳停了"的链，重新入队

④ 是 ①②③ 的**兜底**：只要数据库记着"这条链没跑完"，
   哪怕消息彻底丢了（Redis 重启、ACK 后崩溃），也能被重新捡起来。

**处理一条链的流程**：

    读 pipeline_run → 从 current_stage 的【下一个】开始
      → 逐阶段跑（GPU 阶段先抢锁）
      → 每跑完一个阶段：UPDATE current_stage（这一步之后，崩了也能续）
      → 全跑完：DONE / DEGRADED
    异常：attempt+1，未超限 → 延迟重投；超限 → DEAD + DLQ
    **最后才 XACK**（顺序很重要，见下）
"""
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import redis

from hub.core.config import Settings
from hub.persistence.db import get_session
from hub.persistence.models import PipelineRun
from hub.queue import client as q

_settings = Settings()
_q = _settings.queue


def now() -> datetime:
    return datetime.now(timezone.utc)


def worker_id() -> str:
    """消费者名：`主机名-进程号`。Redis 用它区分 PEL 里的待确认消息归谁。"""
    return f"{socket.gethostname()}-{os.getpid()}"


# ==================== 数据库状态操作 ====================

def _load(run_id: str) -> PipelineRun | None:
    with get_session() as s:
        return s.get(PipelineRun, uuid.UUID(run_id))


def _update(run_id: str, **fields) -> None:
    with get_session() as s:
        row = s.get(PipelineRun, uuid.UUID(run_id))
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()


# ==================== GPU 锁 ====================

def _acquire_gpu(r: redis.Redis, wait_s: int = 900) -> bool:
    """抢 GPU 锁（8GB 显存装不下两个模型同时跑）。

    用 `SET NX EX`：原子"不存在才设置"，且带过期时间——
    worker 崩了锁会在 TTL 后自动释放，不会永久卡死。
    """
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if r.set(_q.gpu_lock_key, worker_id(), nx=True, ex=_q.gpu_lock_ttl_s):
            return True
        time.sleep(5)              # 别人在跑，等一会儿再试
    return False


def _release_gpu(r: redis.Redis) -> None:
    """释放锁（只有持有者能释放）。"""
    if r.get(_q.gpu_lock_key) == worker_id():
        r.delete(_q.gpu_lock_key)


# ==================== 跑一条链 ====================

def process(run_id: str, r: redis.Redis) -> str:
    """跑一条链。返回最终状态（DONE/DEGRADED/SKIPPED）。

    :raises Exception: 任何阶段抛异常都会往外抛，由调用方决定重试还是死信
    """
    from hub.queue.stages import next_stage_after

    row = _load(run_id)
    if row is None:
        print(f"    [跳过] pipeline_run {run_id} 不存在")
        return "SKIPPED"
    if row.status == "DONE" or row.status == "DEGRADED":
        print(f"    [跳过] 这条链已经完成（{row.status}）")
        return "SKIPPED"
    if row.status == "DEAD":
        print("    [跳过] 这条链已进死信，需人工 replay")
        return "SKIPPED"

    # 标记开始（对账器靠 heartbeat 判断"是不是卡死了"）
    _update(run_id, status="RUNNING", started_at=row.started_at or now(),
            heartbeat_at=now(), attempt=(row.attempt or 0) + 1,
            error=None)
    degraded = row.degraded or False

    stage = next_stage_after(row.current_stage)
    print(f"    从阶段 {row.current_stage or '(开头)'} 之后开始")

    while stage is not None:
        print(f"  ── [{stage.name}] {stage.title}"
              f"{'（需要 GPU）' if stage.needs_gpu else ''}")
        t0 = time.time()

        if stage.needs_gpu and not _acquire_gpu(r):
            raise RuntimeError("等 GPU 锁超时（15 分钟）——可能另一个 worker 卡住了")
        try:
            stage.run(row.params or {})
        finally:
            if stage.needs_gpu:
                _release_gpu(r)

        # ★ 关键：**阶段成功之后立刻写数据库**。
        # 这一步之后崩溃，续跑会从下一个阶段开始，不会重跑这一步。
        _update(run_id, current_stage=stage.name, heartbeat_at=now())
        print(f"  ✅ [{stage.name}] 完成（{time.time() - t0:.1f}s）")

        # 有阶段降级了（LLM 抖动/兜底）——链继续跑，但要记一笔
        if _stage_degraded(stage.name):
            degraded = True
            _update(run_id, degraded=True)
            print(f"  ⚠ [{stage.name}] 降级完成（不中断链，但记为 DEGRADED）")

        stage = next_stage_after(stage.name)

    final = "DEGRADED" if degraded else "DONE"
    _update(run_id, status=final, finished_at=now(), heartbeat_at=now())
    print(f"  🏁 链完成：{final}")
    return final


# ==================== PEL 心跳续租 ====================

class PelRenewer:
    """后台线程：定期"摸一下"正在处理的那条消息，重置它的闲置时间。

    **为什么必须有它**：
        Redis 的 XAUTOCLAIM 判断"任务是不是崩了"，看的是**消息的闲置时间**。
        而 worker 跑 M2 要 180 秒，这 180 秒里它**根本不碰 Redis 里那条消息**
        → 闲置时间真的涨到 180 秒 → 抢占阈值必须大于它，否则
        "正在跑的任务"会被当成"崩了的任务"抢走，两个 worker 同时写同一批数据。

    **续租之后**：
        `XCLAIM <msg> JUSTID` 会把闲置时间重置为 0，
        所以阈值可以设得很短（比如 60 秒），只受"续租间隔"约束，**与阶段耗时无关**。
        → 真崩了 1 分钟就被接管（而不是 10 分钟）
        → 新增一个更慢的阶段也不会让阈值失效（不再依赖 est_s 估算）

    **间隔为什么是 claim_idle // 4**：
        阈值 60s → 每 15s 续一次；阈值 5s → 每 1.25s 续一次。
        这样**任何阈值都是安全的**，不用为"演示"留危险开关。
    """

    def __init__(self, r: redis.Redis, msg_id: str, claim_idle_s: int):
        self.r = r
        self.msg_id = msg_id
        self.interval = max(1.0, claim_idle_s / 4.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"pel-renew-{self.msg_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                # justid=True：只返回 ID，**不增加投递计数**（否则重试次数会虚高）
                self.r.xclaim(_q.stream, _q.group, worker_id(),
                              min_idle_time=0, message_ids=[self.msg_id],
                              justid=True)
            except Exception:                 # noqa: BLE001
                pass                           # 续租失败不致命：真崩了照样会被接管


def _all_stages():
    from hub.queue.stages import STAGES
    return STAGES


def _stage_degraded(stage_name: str) -> bool:
    """这个阶段是不是"降级完成"了？

    判据来自各模块自己写的 `analysis_run.status` / `stats`：
        M2 → vision 失败 > 0
        M3 → 有兜底（fallback>0）或有格子树失败
    **不在这里重算业务逻辑**，只读它们留下的痕迹。
    """
    from sqlalchemy import func, select

    from hub.persistence.models import AnalysisRun

    module = {"mm": "M2", "topic": "M3", "propose": "M5"}.get(stage_name)
    if not module:
        return False

    # M5（提案）的"降级"没有 analysis_run.status 可看——它的质量信号在
    # 草稿的合规命中上：出现【红线】（承诺金额/索要隐私）就说明这批提案不能直接发。
    if module == "M5":
        from hub.persistence.models import ReviewReplyDraft
        with get_session() as s:
            latest = s.scalars(select(ReviewReplyDraft)
                               .order_by(ReviewReplyDraft.created_at.desc())
                               .limit(20)).all()
        return any(f.startswith("红线") for d in latest
                   for f in (d.compliance_flags or []))

    with get_session() as s:
        run = s.scalars(select(AnalysisRun)
                        .where(AnalysisRun.stats["module"].astext == module)
                        .order_by(AnalysisRun.created_at.desc()).limit(1)).first()
        if run is None:
            return False
        if run.status == "DEGRADED":
            return True
        st = (run.stats or {}).get("summary") or (run.stats or {})
        return bool(st.get("fallback")) or bool(st.get("failed")) \
            or bool(st.get("failed_cells"))


# ==================== 主循环 ====================

def run(once: bool = False, idle_s: int | None = None,
        verbose: bool = True) -> int:
    """worker 主循环。

    :param once: 只处理一条消息就退出（测试用）
    :param idle_s: 覆盖配置里的"闲置多久算崩了"

    **为什么可以放心调小 idle_s**：
        处理每条消息时会启动 `PelRenewer` 后台线程续租（间隔 = idle_s/4），
        所以"正在跑的任务"的闲置时间永远接近 0，不会被别人抢。
        阈值只影响"真崩了之后多久被接管"，不再是"必须大于最长阶段耗时"。
    """
    claim_idle = idle_s or _q.claim_idle_s
    r = q.get_redis()
    ok, msg = q.ping(r)
    if not ok:
        # Redis 不可用 → 明确退出，不要无限重试（无限重试会把日志刷爆，还没人看）
        print(f"❌ 连不上 Redis：{msg}")
        print("   检查：docker ps --filter name=hub-redis")
        print("         .env 里的 HUB_QUEUE__URL 是否正确")
        return 2
    q.ensure_group(r)

    # 阈值下限：太小会让"续租间隔"变得过于频繁（idle_s/4）。
    # 注意这里**不再检查"是否大于最长阶段耗时"**——心跳续租之后那个约束不存在了。
    if claim_idle < 4:
        print(f"❌ 抢占阈值 {claim_idle}s 太小（至少 4 秒）")
        return 2

    me = worker_id()
    print(f"worker 启动：{me}")
    print(f"  队列 {_q.stream}｜消费组 {_q.group}｜抢占阈值 {claim_idle}s"
          f"｜最多重试 {_q.max_attempts} 次")
    print("  Ctrl+C 退出（当前阶段跑完再停，不 ACK 让任务可重投）\n")

    last_claim = 0.0
    last_reconcile = 0.0
    processed = 0
    current_msg = None          # (msg_id, run_id)

    try:
        while True:
            # ---- ② 搬延迟重试 ----
            for rid in q.pop_due_delayed(r):
                q.enqueue(r, rid)
                if verbose:
                    print(f"  ↻ 延迟到期，重新入队 {rid[:8]}")

            # ---- ③④ 每 60 秒：抢闲置 + 对账 ----
            if time.time() - last_claim > 60:
                _claim_stale(r, me, claim_idle, verbose)
                last_claim = time.time()
            if time.time() - last_reconcile > 60:
                _reconcile(r, claim_idle, verbose)
                last_reconcile = time.time()

            # ---- ① 领任务 ----
            resp = r.xreadgroup(_q.group, me,
                                {_q.stream: ">"}, count=1, block=5000)
            if not resp:
                if once:
                    # `--once` 的语义是"处理完一条就退"，
                    # 队列空的时候也该退——否则测试/脚本会一直挂在这里。
                    print("\n--once 模式：队列为空，退出", flush=True)
                    return 0
                continue
            stream, messages = resp[0]
            msg_id, fields = messages[0]
            run_id = fields.get("run_id", "")
            current_msg = (msg_id, run_id)
            print(f"\n▶ 领到任务 {run_id[:8]}（消息 {msg_id}）")

            # ★ 心跳续租：从领到消息开始，后台线程每 idle_s/4 秒摸一下，
            #   保证"正在跑的任务"的闲置时间永远接近 0（不会被当成崩了抢走）。
            renewer = PelRenewer(r, msg_id, claim_idle)
            renewer.start()
            try:
                process(run_id, r)
                r.xack(_q.stream, _q.group, msg_id)   # ★ 跑完才 ACK
                current_msg = None
                processed += 1
            except KeyboardInterrupt:
                raise
            except Exception as e:                    # noqa: BLE001
                current_msg = None                    # 交给重试逻辑处理
                _handle_failure(r, run_id, e, msg_id)
            finally:
                renewer.stop()
            if once:
                print(f"\n--once 模式：处理了 {processed} 条，退出", flush=True)
                return 0

    except KeyboardInterrupt:
        print("\n\n收到 Ctrl+C。")
        if current_msg:
            # **故意不 ACK**：任务留在 PEL，10 分钟后被别的 worker 抢走，
            # 数据库里有断点，所以接着跑而不是从头。
            print(f"  当前任务 {current_msg[1][:8]} 未 ACK —— 会由其它 worker 接管"
                  f"（数据库里有断点，不会从头跑）")
        _release_gpu(r)
        return 0


def _handle_failure(r: redis.Redis, run_id: str, e: Exception, msg_id: str) -> None:
    """失败处理：未超限 → 延迟重投；超限 → 死信 + DEAD。"""
    row = _load(run_id)
    attempt = (row.attempt or 1) if row else 1
    err = f"{type(e).__name__}: {e}"
    _update(run_id, error=err[:500], heartbeat_at=now())
    # 注意措辞：attempt 在【进入任务时】就 +1 了，所以它表示"第几次尝试"，
    # 不是"第几次失败"。写清楚，否则排查时会数错。
    print(f"  ❌ 第 {attempt} 次尝试失败：{err[:120]}")

    if attempt >= _q.max_attempts:
        _update(run_id, status="DEAD", finished_at=now())
        q.to_dlq(r, run_id, err)
        r.xack(_q.stream, _q.group, msg_id)        # 死信了就别再重投
        print(f"  ☠ 已达 {_q.max_attempts} 次，进死信。"
              f"处理：hub dlq replay --id {run_id[:8]}")
        return

    delay = _q.backoff_s[min(attempt - 1, len(_q.backoff_s) - 1)]
    _update(run_id, status="QUEUED")               # 回到待处理，等延迟队列
    q.enqueue_delayed(r, run_id, delay)
    r.xack(_q.stream, _q.group, msg_id)            # 原消息 ACK 掉，靠 ZSET 重投
    print(f"  ⏳ 等 {delay} 秒后重试")


def _claim_stale(r: redis.Redis, me: str, idle_s: int, verbose: bool) -> None:
    """③ 抢闲置任务：别的 worker 崩了，它的消息留在 PEL 里。

    用 XAUTOCLAIM（Redis 6.2+）：一次调用就能"扫 + 认领"，不用先 XPENDING 再 XCLAIM。
    """
    try:
        resp = r.xautoclaim(_q.stream, _q.group, me,
                            min_idle_time=idle_s * 1000, start_id="0-0", count=10)
    except redis.ResponseError:
        return                                     # 老版本 Redis 没有这个命令
    messages = resp[1] if len(resp) > 1 else []
    for msg_id, fields in messages:
        run_id = (fields or {}).get("run_id", "")
        if verbose:
            print(f"  🔧 抢到闲置任务 {run_id[:8]}（原 worker 可能崩了）")
        renewer = PelRenewer(r, msg_id, claim_idle)
        renewer.start()
        try:
            process(run_id, r)
            r.xack(_q.stream, _q.group, msg_id)
        except Exception as e:                     # noqa: BLE001
            _handle_failure(r, run_id, e, msg_id)
        finally:
            renewer.stop()


def _reconcile(r: redis.Redis, idle_s: int, verbose: bool) -> None:
    """④ 对账：数据库说 RUNNING、但心跳停了 → 重新入队。

    **这是最后一道保险**。①②③ 处理的是"消息还在 Redis 里"的情况；
    如果消息彻底没了（Redis 重启没开 AOF、ACK 后立刻崩溃），只有这里能发现。
    """
    from sqlalchemy import select

    deadline = now() - timedelta(seconds=idle_s)
    with get_session() as s:
        stale = s.scalars(select(PipelineRun)
                          .where(PipelineRun.status == "RUNNING")
                          .where(PipelineRun.heartbeat_at < deadline)).all()
        ids = [(str(x.id), x.current_stage) for x in stale]
        for run_id, _ in ids:
            row = s.get(PipelineRun, uuid.UUID(run_id))
            row.status = "QUEUED"                  # 回到待跑，断点还在
        s.commit()
    for run_id, stage in ids:
        q.enqueue(r, run_id)
        if verbose:
            print(f"  🩺 对账：{run_id[:8]}（断点 {stage}）心跳超时，重新入队")
