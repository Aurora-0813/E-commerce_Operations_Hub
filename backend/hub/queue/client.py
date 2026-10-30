# -*- coding: utf-8 -*-
"""
Redis 客户端与队列原语（client.py）
==================================
把"Redis 长什么样"全部收在这里，其余模块只调这几个函数。

**消息格式故意做到极简**：

    {"run_id": "<pipeline_run.id>", "enqueued_at": "..."}

就这么两个字段。因为 **trace_id / 阶段 / 参数 / 重试次数全在数据库里**
（`pipeline_run` 那行）。消息只需要回答一件事：**该跑哪条链了**。

对比设计文档的 6 字段消息 `{task_id, type, trace_id, batch_id, tenant_id, attempt}`
——那套是"消息即真相"，所以消息必须把状态全带上；我们把真相放数据库，
消息就能瘦下来，也就不存在"消息和数据库不一致"的问题。
"""
from datetime import datetime, timezone

import redis

from hub.core.config import Settings

_settings = Settings()
_q = _settings.queue


def get_redis() -> redis.Redis:
    """取 Redis 连接。

    两个超时都必须设：
        socket_connect_timeout —— Redis 没起来时【5 秒内】报错，
                                  而不是让 Windows 的 TCP 超时决定你等 2 分钟
        socket_timeout         —— 命令卡住时的上限
    """
    return redis.Redis.from_url(
        _q.url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=30,
    )


def ping(r: redis.Redis | None = None) -> tuple[bool, str]:
    """探活：返回 (是否可用, 说明)。"""
    try:
        r = r or get_redis()
        r.ping()
        info = r.info("server")
        return True, f"Redis {info.get('redis_version', '?')} 正常"
    except Exception as e:                       # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def ensure_group(r: redis.Redis) -> None:
    """确保消费组存在。

    `id="0"` 而不是 `"$"`：
        `"$"` 表示"只消费建组之后的消息"——如果任务在 worker 启动【之前】入队，
        它会被永远跳过。`"0"` 表示"从头消费"，配合幂等，重复无害、遗漏有害。
    `MKSTREAM`：Stream 不存在时顺手建出来（否则第一次运行会报 NOGROUP）。
    """
    try:
        r.xgroup_create(_q.stream, _q.group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):            # 组已存在是最常见的情况，不算错
            raise


def enqueue(r: redis.Redis, run_id: str) -> str:
    """把一条链塞进队列，返回 Redis 的消息 ID。"""
    msg_id = r.xadd(_q.stream, {
        "run_id": run_id,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    })
    return msg_id


def enqueue_delayed(r: redis.Redis, run_id: str, delay_s: int) -> None:
    """延迟重试：用 ZSET 当延迟队列。

    Stream 没有"延迟投递"，所以把消息放进 ZSET，
    score = 到期时间戳；有个线程定期把到期的搬回 Stream。
    """
    due = datetime.now(timezone.utc).timestamp() + delay_s
    r.zadd(_q.delayed_key, {run_id: due})


def pop_due_delayed(r: redis.Redis, limit: int = 50) -> list[str]:
    """取出**已到期**的 run_id（顺便从 ZSET 删掉）。"""
    now = datetime.now(timezone.utc).timestamp()
    due = r.zrangebyscore(_q.delayed_key, 0, now, start=0, num=limit)
    if due:
        r.zrem(_q.delayed_key, *due)
    return list(due)


def to_dlq(r: redis.Redis, run_id: str, error: str) -> None:
    """进死信。"""
    r.xadd(_q.dlq_key, {
        "run_id": run_id,
        "error": (error or "")[:500],
        "failed_at": datetime.now(timezone.utc).isoformat(),
    })


def queue_stats(r: redis.Redis) -> dict:
    """队列状态（给 `hub queue` 和 `hub health` 用）。"""
    try:
        depth = r.xlen(_q.stream)
    except redis.ResponseError:
        depth = 0
    try:
        pending = r.xpending(_q.stream, _q.group)
        pel = pending.get("pending", 0) if isinstance(pending, dict) else 0
    except redis.ResponseError:
        pel = 0
    return {
        "depth": depth,
        "pending": pel,
        "delayed": r.zcard(_q.delayed_key),
        "dlq": r.xlen(_q.dlq_key) if _exists(r, _q.dlq_key) else 0,
    }


def _exists(r: redis.Redis, key: str) -> bool:
    return bool(r.exists(key))
