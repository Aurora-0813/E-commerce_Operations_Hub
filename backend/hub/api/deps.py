# -*- coding: utf-8 -*-
"""
API 共享依赖（deps.py）—— 修循环导入（2026-11-01）
================================================
**这个文件是为什么存在的**：

    原来 `AuthUser` / `get_current_user` / `_trace_id` / `require_role`
    都定义在 `hub/api/main.py` 里 ✅
    → 而 `main.py` 在**模块级**就跑 `app = create_app()` ✅
    → `create_app()` 里又 `from hub.api.read import router` ✅
    → 而 `read.py` 又 `from hub.api.main import AuthUser` ✅
    → **如果先 import `read`**：main 还没定义完 → **循环导入 ImportError** ✅ ✗
      （先 import `main` 就没事 → 所以这个 bug **只在某些导入顺序下出现** ✅
       实践上没炸，但那是"运气"，不是"设计" ✅）

**修法**：把这些**共享的小东西**搬到一个**不创建 app** 的模块里 ✅
    `main.py` → `deps.py`（单向 ✅）
    `read.py` / `charts.py` / `write_*.py` → `deps.py`（单向 ✅）
    **没有任何模块从 `main` 导入东西了** ✅ → 环断了 ✅
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status


@dataclass
class AuthUser:
    """从 JWT 解析出来的"当前用户"。

    **只放身份，不放权限明细** ✅
        权限由 `ToolSpec.required_role` 决定（工具的属性，不是用户的属性）✅
        这样改权限不用等 token 过期 ✅
    """
    id: str
    email: str
    role: str
    tenant_id: str


def _trace_id(request: Request) -> str:
    """每次请求一个 trace_id（如果调用方给了 `X-Trace-Id` 就沿用 ✅）"""
    return request.headers.get("x-trace-id") or f"api-{uuid.uuid4().hex[:12]}"


def get_current_user(request: Request) -> AuthUser:
    """从 `Authorization: Bearer <jwt>` 解析当前用户。

    ⚠️ **任何失败都是 401，且原因分三档**（过期 / 格式错 / 缺 header）✅
       为什么分档：用户看到"token 已过期，请重新登录"才知道该怎么办 ✅
       为什么都算 401：401 = "你是谁我不知道" ✅（403 是"我知道你是谁，但你不能做" ✅）
    """
    from hub.core.auth import TokenInvalid, decode_token

    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "NO_TOKEN", "message": "缺少 Authorization: Bearer <token>",
                    "trace_id": _trace_id(request)},
        )
    token = auth.split(" ", 1)[1].strip()
    try:
        p = decode_token(token)
    except TokenInvalid as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_INVALID", "message": str(e),
                    "trace_id": _trace_id(request)},
        ) from None

    return AuthUser(id=str(p["sub"]), email=str(p.get("email") or ""),
                    role=str(p["role"]), tenant_id=str(p.get("tenant") or "default"))


def require_role(min_role: str):
    """生成一个"至少需要某角色"的依赖。

    角色序复用 `CallContext.has_role` 的同一套
    （system < analyst < operator < supervisor < admin ✅）
    —— **不要让 API 层和工具层各有一套** ✅
    """
    def _dep(request: Request,
             user: AuthUser = Depends(get_current_user)) -> AuthUser:
        from hub.tools.spec import CallContext
        if not CallContext(role=user.role).has_role(min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ROLE_TOO_LOW",
                        "message": f"需要 {min_role} 及以上角色，你是 {user.role}",
                        "trace_id": _trace_id(request)},
            )
        return user
    return _dep
