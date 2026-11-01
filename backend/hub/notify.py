# -*- coding: utf-8 -*-
"""
通知 outbox（notify.py）—— 阶段 3.7
==================================
路线图 §9.5.1 的原话：

    "outbox 模式（**先落库，再派发**）：
        业务事件 → 写 `notification`(status=PENDING) → 异步派发 → 标 SENT/FAILED
     **为什么用 outbox 而不是『直接发』**：
        直接发 → 网络抖动就丢了；而且『发没发』没记录 ✅
        outbox → 幂等（同事件只发一次）、可重试、可审计 ✅"

**堵的缺口**：`hub today` 假设"有人每天会打开"，但真实运营不会 ✅
    → P0 的簇/待批任务，没人刷新页面就烂在那 ✅

**三个设计决定**（都在代码里写清理由）：

    ① **幂等键 = (event_type, resource_id, channel)**
       `notification` 表**没有** idempotency_key 列 ✅
       → 用这三列的自然组合判定"这条事件是否已经写过" ✅
       （不新建表、不加列 —— 路线图说表全都在 ✅）

    ② **合并**：同一 `event_type` 在 10 分钟内有 PENDING → 追加进它的 `payload["items"]`
       为什么："5 个提案待批"发 5 条 → 人会关通知 ✅（路线图原话 ✅）

    ③ **没配 webhook 时 → status=SKIPPED + reason**
       为什么不标 SENT：**没发就是没发** ✅ 标 SENT 是在骗审计 ✅
       为什么不报错：本地开发没配 webhook 是常态 ✅
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

MERGE_WINDOW_MIN = 10
MAX_ATTEMPTS = 3          # 失败重试上限（**不无限重试** ✅）


def _cfg():
    """读通知配置（全在 `.env` 里，**不硬编码** ✅）。"""
    try:
        from hub.core.config import Settings
        c = getattr(Settings(), "notify", None)
        return {
            "webhook_url": getattr(c, "webhook_url", "") or "",
            "webhook_timeout": int(getattr(c, "webhook_timeout_s", 5) or 5),
        }
    except Exception:                             # noqa: BLE001
        return {"webhook_url": "", "webhook_timeout": 5}


# ════════════════════════════════════════════════════ ① 落库（outbox）
def emit(event_type: str, resource_id: str, payload: dict | None = None,
         *, channel: str = "webhook", recipient: str = "") -> int:
    """写一条待发通知（PENDING）。**幂等**：同一事件不重复写 ✅

    :return: notification.id（已存在时返回已有的 id ✅）
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import Notification

    with get_session() as s:
        # ---- ② 合并：同一 event_type 10 分钟内有 PENDING → 追加 ✅ ----
        since = datetime.now(timezone.utc) - timedelta(minutes=MERGE_WINDOW_MIN)
        pend = s.scalars(select(Notification).where(
            Notification.event_type == event_type,
            Notification.channel == channel,
            Notification.status == "PENDING",
            Notification.created_at >= since,
        ).order_by(Notification.id.desc())).first()

        # ---- ① 幂等：这个 resource_id 已经在这条的 items 里了吗 ✅ ----
        if pend is not None:
            pl = dict(pend.payload or {})
            items = list(pl.get("items") or [])
            if any(it.get("resource_id") == resource_id for it in items):
                return int(pend.id)                    # 已经写过 ✅
            items.append({"resource_id": resource_id, **(payload or {})})
            pl["items"] = items
            pl["count"] = len(items)
            pend.payload = pl
            s.commit()
            return int(pend.id)

        row = Notification(event_type=event_type, resource_id=resource_id,
                           channel=channel, recipient=recipient or "",
                           payload={"items": [{"resource_id": resource_id,
                                               **(payload or {})}],
                                    "count": 1},
                           status="PENDING", attempts=0)
        s.add(row)
        try:
            s.flush()
        except Exception:
            # ⚠️ `notification` 表有 **`uq_notification_idem`**
            #    ON (event_type, resource_id, channel, recipient) ✅
            #    → "同一事件只发一次"是**数据库保证的** ✅
            #    而上面只查了"10 分钟内的 PENDING" ✅
            #    那条通知如果已经是 SENT/SKIPPED/FAILED → 上面查不到 → 撞约束 ✅ ✗
            #    → **接住冲突，返回已有的那条** ✅（这才是真幂等 ✅）
            s.rollback()
            exist = s.scalars(select(Notification).where(
                Notification.event_type == event_type,
                Notification.resource_id == resource_id,
                Notification.channel == channel,
                Notification.recipient == (recipient or ""),
            )).first()
            if exist is not None:
                return int(exist.id)
            raise
        nid = int(row.id)
        s.commit()
        return nid


# ════════════════════════════════════════════════════ ③ 派发
def _render(ev: str, payload: dict) -> str:
    """把 payload 渲染成人话（webhook 的 body ✅）。"""
    items = payload.get("items") or []
    head = {
        "proposal.pending": "🔴 有提案待批",
        "retro.due": "🔵 有动作到了复盘窗口",
        "dead_letter": "💀 有任务进了死信队列",
        "agent.done": "🤖 agent 跑完了",
    }.get(ev, f"📌 {ev}")
    lines = [f"{head}（{len(items)} 条）"]
    for it in items[:10]:
        lines.append(f"- {it.get('resource_id')}　{it.get('summary', '')}")
    if len(items) > 10:
        lines.append(f"… 还有 {len(items) - 10} 条")
    return "\n".join(lines)


def dispatch(limit: int = 20) -> dict:
    """把 PENDING 的通知发出去。返回统计。

    **先落库再派发**：即使这里崩了，PENDING 还在 ✅ 下次还能发 ✅
    """
    import urllib.error
    import urllib.request

    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import Notification

    cfg = _cfg()
    url = cfg["webhook_url"]
    stat = {"picked": 0, "sent": 0, "skipped": 0, "failed": 0, "retry_exhausted": 0}

    with get_session() as s:
        rows = s.scalars(select(Notification).where(
            Notification.status == "PENDING"
        ).order_by(Notification.id).limit(limit)).all()
        for n in rows:
            stat["picked"] += 1
            if not url:
                # 没配 webhook → **诚实标 SKIPPED**（不假装发过 ✅）
                n.status = "SKIPPED"
                n.payload = dict(n.payload or {}, skip_reason="没配 HUB_NOTIFY__WEBHOOK_URL")
                # ⚠️ **必须记账**（2026-11-01 踩到）：
                #    第一版只改了 status，没加 `stat["skipped"]` ✅ ✗
                #    → 输出 `picked: 2 / skipped: 0`，看着像"没处理" ✅
                stat["skipped"] += 1
                continue
            if int(n.attempts or 0) >= MAX_ATTEMPTS:
                n.status = "FAILED"
                stat["retry_exhausted"] += 1
                continue
            body = json.dumps({"text": _render(n.event_type, n.payload or {}),
                               "event_type": n.event_type,
                               "count": (n.payload or {}).get("count", 1)},
                              ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=cfg["webhook_timeout"]) as resp:
                    ok = 200 <= resp.status < 300
                if ok:
                    n.status = "SENT"
                    n.sent_at = datetime.now(timezone.utc)
                    stat["sent"] += 1
                else:
                    raise RuntimeError(f"HTTP {resp.status}")
            except Exception as e:                # noqa: BLE001
                n.attempts = int(n.attempts or 0) + 1
                n.payload = dict(n.payload or {},
                                 last_error=f"{type(e).__name__}: {e}"[:200])
                stat["failed"] += 1
                # 没到上限就**保持 PENDING** 等下次重试 ✅（不无限 ✅）
                if n.attempts >= MAX_ATTEMPTS:
                    n.status = "FAILED"
        s.commit()
    return stat


# ════════════════════════════════════════════════════ 触发源
def scan_and_emit() -> dict:
    """扫已有业务表，把该通知的事件写进 outbox（**复用已有数据，不新建表** ✅）。

    四个触发源（路线图 §9.5.1）：
        ① `hitl_task` PENDING      → 提案待批
        ② `action_outcome` WAITING 且 observe_end 已过 → 复盘到期
        ③ `dead_letter`            → 死信
        ④ `agent_run` NEEDS_HUMAN  → agent 需要人
    """
    out = {"proposal.pending": 0, "retro.due": 0, "dead_letter": 0, "agent.done": 0}
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import (ActionOutcome, AgentRun, DeadLetter,
                                        HitlTask)

    with get_session() as s:
        for t in s.scalars(select(HitlTask).where(HitlTask.status == "PENDING")).all():
            emit("proposal.pending", str(t.proposal_id),
                 {"summary": f"待批（{t.trace_id}）"})
            out["proposal.pending"] += 1
        # ⚠️ `action_outcome.observe_end` 是 **DATE**（不是 datetime ✅ 2026-11-01 核对）
        #    → 和 `datetime` 比会 TypeError ✅ ✗ → 用 `date.today()` ✅
        from datetime import date as _date
        today = _date.today()
        for o in s.scalars(select(ActionOutcome).where(
                ActionOutcome.status == "WAITING")).all():
            oe = o.observe_end
            oe_date = oe.date() if hasattr(oe, "date") else oe
            if oe_date and oe_date <= today:
                emit("retro.due", str(o.proposal_id), {"summary": "观察窗口已到"})
                out["retro.due"] += 1
        for d in s.scalars(select(DeadLetter).limit(50)).all():
            emit("dead_letter", str(d.id), {"summary": (d.error or "")[:80]})
            out["dead_letter"] += 1
        for r in s.scalars(select(AgentRun).where(
                AgentRun.status == "NEEDS_HUMAN").limit(50)).all():
            emit("agent.done", str(r.id), {"summary": "agent 转人工"})
            out["agent.done"] += 1
    return out


def pending_count() -> int:
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import Notification
    with get_session() as s:
        return int(s.scalar(select(func.count()).select_from(Notification)
                            .where(Notification.status == "PENDING")) or 0)
