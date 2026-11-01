# -*- coding: utf-8 -*-
"""
审计写入（audit.py）—— 阶段 4.2
==============================
**为什么单独一个模块**：
    路线图 §10.2 要求"**在业务层写，不在 API 层拦截**" ✅
    因为 `hub approve` / `hub dlq` 这些 CLI 也要记 ✅
    → 所以写审计是一个**业务层的能力**，API 只是其中一个调用方 ✅

**schema 按现有走**（路线图原话："按现有 schema 走，不要另起一套" ✅）：
    `audit_log(tenant_id, actor, action, resource, detail, client_ip, created_at)`
    注意它用的是 **`resource` 单字符串**（如 `proposal:<id>`），
    不是 `target_type` + `target_id` 两列 ✅

**actor 的写法**（很重要）：
    `user:<uuid>`      —— 真人（阶段 4 有了登录才有 ✅）
    `cli`              —— 命令行（可 `--as` 覆盖 ✅）
    `system:<模块>`    —— 系统自动（如 `system:cloud_guard` ✅）
    `anon:<email>`     —— 未登录且登录失败（只记邮箱，不记密码 ✅）
    **不可为空** ✅（空 actor 的审计等于没记 ✅）
"""
from __future__ import annotations

import sys


def write(*, actor: str, action: str, resource: str,
          detail: dict | None = None, ip: str | None = None,
          trace_id: str | None = None, tenant_id: str = "default") -> None:
    """写一条审计。**失败不阻断业务** ✅（但会打警告 ✅）

    为什么 fail-open：
        审计表写不进去（比如序列问题）时，业务**不该跟着失败** ✅
        代价是"可能漏一条审计" —— 比"因为日志写不进就不让运营干活"轻 ✅
        （真正的合规场景要 fail-closed；这里是内部运营系统，选 fail-open ✅）
    """
    if not actor:
        # 不允许"无主"的审计 ✅
        actor = "system:unknown"
        print("[audit] ⚠️ actor 为空，记为 system:unknown", file=sys.stderr)

    # trace_id 拼进 detail（`audit_log` 没有单独列 ✅ 按现有 schema 走 ✅）
    d = dict(detail or {})
    if trace_id:
        d["trace_id"] = trace_id

    # ⚠️ `client_ip` 是 **INET 类型** ✅ → 必须是合法 IP 或 NULL ✅
    #    踩过：TestClient 传的是 `"testclient"` → `InvalidTextRepresentation`
    #    → 整条审计写入失败 ✅ ✗（而 fail-open 让它**静默丢失** ✅）
    #    修法：**IP 不合法就写 NULL**，而不是丢掉整条审计 ✅
    ip_ok = None
    if ip:
        try:
            import ipaddress
            ip_ok = str(ipaddress.ip_address(str(ip)))
        except ValueError:
            ip_ok = None

    try:
        from hub.persistence.db import get_session
        from hub.persistence.models import AuditLog

        with get_session() as s:
            s.add(AuditLog(tenant_id=tenant_id, actor=actor, action=action,
                           resource=resource, detail=d, client_ip=ip_ok))
            s.commit()
    except Exception as e:                      # noqa: BLE001
        print(f"[audit] ⚠️ 写审计失败（不阻断业务）：{type(e).__name__}: {e}",
              file=sys.stderr)
