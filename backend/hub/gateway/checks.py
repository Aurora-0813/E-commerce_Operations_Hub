# -*- coding: utf-8 -*-
"""
网关的三道辅助检查（checks.py）
==============================
    evidence   依据校验（含"对得上商品"的语义检查）
    idempotency 幂等（DB 唯一索引，两段式：先查后占位）
    ratelimit  频控（进程内令牌桶）

**为什么都抽成独立模块**：
    以后换 Redis（多实例部署）只改 idempotency / ratelimit 的实现，
    网关的 invoke() 一行不用动。
"""
import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import (
    Anomaly,
    IdempotencyRecord,
    IssueCluster,
    SkuMapping,
)
from hub.tools.spec import CallContext, ToolSpec


# ==================== 依据校验 ====================

class EvidenceError(Exception):
    """依据校验失败。code 是 DenyCode 的字符串值。"""
    def __init__(self, code: str, detail: dict):
        super().__init__(code)
        self.code = code
        self.detail = detail


def check_evidence(spec: ToolSpec, args: dict, refs: dict) -> None:
    """校验依据。不合格抛 EvidenceError。

    三步：
        ① **类别齐全**：工具声明要哪几类依据，就必须都提供
        ② **真实存在**：引用的簇/异常在库里真的有
        ③ **对得上**（条件化）：
              args 里有 spu_id   → 依据必须覆盖这个商品
              args 里有 cluster_id → 依据必须就是那个簇
           没有 spu/cluster 的工具（如区域级动作）只做前两步。
    """
    if not spec.evidence_types:
        return                          # L0 只读工具不需要依据

    missing = [t for t in spec.evidence_types if not refs.get(t)]
    if missing:
        raise EvidenceError("EVIDENCE_MISSING", {"missing": missing})

    with get_session() as s:
        if "issue_cluster" in spec.evidence_types:
            _check_cluster(s, args, str(refs["issue_cluster"]))
        if "anomaly" in spec.evidence_types:
            _check_anomaly(s, args, refs["anomaly"])


def _check_cluster(s, args: dict, cluster_id: str) -> None:
    """簇依据：存在 + 对得上（商品 / 簇 ID）。"""
    try:
        cid = UUID(cluster_id)
    except (ValueError, AttributeError):
        raise EvidenceError("EVIDENCE_NOT_FOUND", {"issue_cluster": cluster_id})

    cluster = s.get(IssueCluster, cid)
    if cluster is None:
        raise EvidenceError("EVIDENCE_NOT_FOUND", {"issue_cluster": cluster_id})

    # 条件化检查 1：args 里指定了簇 → 必须是同一个
    want_cid = args.get("cluster_id")
    if want_cid and str(want_cid) != str(cluster.id):
        raise EvidenceError("EVIDENCE_MISMATCH", {
            "reason": "依据的簇和动作指向的簇不一致",
            "evidence_cluster": str(cluster.id),
            "args_cluster": str(want_cid),
        })

    # 条件化检查 2：args 里指定了商品 → 依据必须覆盖这个商品
    spu = args.get("spu_id")
    if spu and spu not in (cluster.spu_ids or []):
        raise EvidenceError("EVIDENCE_MISMATCH", {
            "reason": "拿 A 问题的证据去改 B 商品",
            "args_spu": spu,
            "cluster_spus": (cluster.spu_ids or [])[:10],
        })


def _check_anomaly(s, args: dict, anomaly_ref) -> None:
    """异常依据：存在 + 对得上商品。

    注意：`anomaly.canonical_spu_id` 存的是**对齐后的 canonical ID**（SPU-0010），
    而工具参数里是**平台商品 ID**（TB-0006）。两者要通过 sku_mapping 对上，
    这是"跨平台"这个项目特有的坑。
    """
    try:
        aid = UUID(str(anomaly_ref))
    except (ValueError, AttributeError):
        raise EvidenceError("EVIDENCE_NOT_FOUND", {"anomaly": str(anomaly_ref)})

    row = s.get(Anomaly, aid)
    if row is None:
        raise EvidenceError("EVIDENCE_NOT_FOUND", {"anomaly": str(anomaly_ref)})

    spu = args.get("spu_id")
    platform = args.get("platform")
    if not spu:
        return
    # 这个平台商品属于哪个 canonical
    canonical = s.scalars(
        select(SkuMapping.canonical_spu_id)
        .where(SkuMapping.platform == platform)
        .where(SkuMapping.platform_spu_id == spu)
        .limit(1)
    ).first()
    if canonical is None or canonical != row.canonical_spu_id:
        raise EvidenceError("EVIDENCE_MISMATCH", {
            "reason": "异常指向的商品和动作指向的商品不一致",
            "anomaly_spu": row.canonical_spu_id,
            "args_spu": spu,
            "args_canonical": canonical,
        })


# ==================== 幂等 ====================

def idem_key(spec: ToolSpec, args: dict, ctx: CallContext,
             window: str | None = None) -> str:
    """幂等键 = sha256(工具@版本 | 租户 | 业务窗口 | 参数)。

    **为什么不带 run_id**：
        带 run_id 的话，"重跑一次管线"就会重复执行——而重跑恰恰是最常见的场景。
        用**日期窗口**（默认今天）更符合业务直觉：同一个动作一天内只做一次。

    参数要**规范化**（key 排序），否则 {"a":1,"b":2} 和 {"b":2,"a":1} 会被当成两次。
    """
    window = window or date.today().isoformat()
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    raw = f"{spec.name}@{spec.version}|{ctx.tenant_id}|{window}|{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def idem_lookup(key: str) -> str | None:
    """第一段：查。命中返回上次的 tool_execution.id（用于重放）。

    查询放在频控**之前**：重复请求应该快速返回，而不是先被限流。
    """
    with get_session() as s:
        row = s.get(IdempotencyRecord, key)
        if row is None:
            return None
        if row.expires_at < datetime.now(timezone.utc):
            s.delete(row)              # 过期就清掉，允许重做
            s.commit()
            return None
        return str(row.result_ref) if row.result_ref else None


def idem_claim(key: str, spec: ToolSpec, execution_id: str) -> bool:
    """第二段：占位。成功返回 True；被别人抢先返回 False。

    占位放在频控**之后**：被限流的请求不该白占一个幂等键。
    """
    with get_session() as s:
        exists = s.get(IdempotencyRecord, key)
        if exists is not None:
            return False
        s.add(IdempotencyRecord(
            key=key,
            tool_name=spec.name,
            result_ref=UUID(execution_id),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        try:
            s.commit()
        except Exception:              # noqa: BLE001  并发撞主键
            s.rollback()
            return False
        return True


def idem_release(key: str) -> None:
    """释放占位（可重试错误时用：允许重来）。"""
    with get_session() as s:
        row = s.get(IdempotencyRecord, key)
        if row is not None:
            s.delete(row)
            s.commit()


# ==================== 频控（进程内令牌桶） ====================

class RateLimiter:
    """进程内令牌桶。

    为什么不用 Redis：
        单进程 demo 够用；真要多实例部署时换成 Redis + Lua 即可，
        调用方只依赖 `acquire()` 这个接口。
    """

    def __init__(self, qps: float = 5.0, burst: int = 10):
        self.qps = qps
        self.burst = burst
        self._bucket: dict[str, deque] = defaultdict(deque)

    def acquire(self, key: str) -> bool:
        """取一个令牌。取不到返回 False（网关据此拒绝）。"""
        now = time.monotonic()
        q = self._bucket[key]
        # 丢掉窗口外的
        while q and now - q[0] > self.burst / self.qps:
            q.popleft()
        if len(q) >= self.burst:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self._bucket.clear()


# 全局单例（CLI 进程内共享）
LIMITER = RateLimiter(qps=5.0, burst=10)
