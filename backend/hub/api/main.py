# -*- coding: utf-8 -*-
"""
FastAPI 入口（main.py）—— 阶段 4.1：认证
========================================
批 1 只做认证（路线图 §10.1）：
    POST /api/auth/login   邮箱 + 密码 → JWT（30 分钟）
    GET  /api/auth/me      用 token 换回自己的身份

**为什么认证必须最先做**（路线图原话）：
    "认证必须在所有接口之前，否则接口写完还要回头加中间件" ✅
    → 所以批 1 就把 `get_current_user` / `require_role` 定下来，
      后面 21 个读接口**全部直接套** ✅

**401/403 必须带 trace_id**（路线图 §10.4 通用要求）：
    用户报"我登录不上"时，能靠 trace_id 去 `hub trace` 查这次请求 ✅
    → 所以每个错误响应体里都有 trace_id ✅

跑法：
    python -m uvicorn hub.api.main:app --reload --port 8000
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ==================================================================== 数据
# ============================================================
#  共享依赖已抽到 `hub/api/deps.py`（2026-11-01，修循环导入 ✅）
# ============================================================
from hub.api.deps import (  # noqa: E402
    AuthUser, _trace_id, get_current_user, require_role)


# ================================================================ 请求/响应模型
# ⚠️ 这些**必须留在 main.py**（只有认证接口用它们 ✅ 别的 router 不用 ✅）
#    2026-11-01 抽 deps 时我一度把它们一起删了 → `NameError: LoginOut` ✅ ✗
from pydantic import BaseModel, Field  # noqa: E402


class LoginIn(BaseModel):
    email: str = Field(description="登录邮箱")
    password: str = Field(min_length=1, description="密码")


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int                      # 秒
    user: UserOut


# ==================================================================== 应用
def create_app() -> FastAPI:
    app = FastAPI(title="电商运营决策中枢 API", version="0.1.0",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    # ---------- CORS（路线图 §10.3）----------
    # ⚠️ `allow_origins=["*"]` + `allow_credentials=True` 是浏览器**会拒绝**的组合 ✅
    #    所以白名单必须写具体域名 ✅
    from hub.core.config import Settings
    origins = getattr(Settings().app, "cors_origins", None) or \
        ["http://localhost:5173", "http://127.0.0.1:5173"]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])

    # ---------- 统一错误：**一律带 trace_id**（路线图 §10.4）----------
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "trace_id" in detail:
            body, tid = detail, detail.get("trace_id") or _trace_id(request)
            body["trace_id"] = tid or _trace_id(request)
        else:
            tid = _trace_id(request)
            body = {"code": f"HTTP_{exc.status_code}", "message": str(detail),
                    "trace_id": tid}
        return JSONResponse(status_code=exc.status_code, content=body)

    # ---------- 健康检查（不要鉴权；前端探活 / 负载均衡用 ✅）----------
    @app.get("/api/ping")
    def ping():
        return {"ok": True, "ts": int(time.time())}

    # ---------- 认证 ----------
    @app.post("/api/auth/login", response_model=LoginOut)
    def login(body: LoginIn, request: Request):
        """邮箱 + 密码 → JWT。

        ⚠️ **失败一律同一句话**（"邮箱或密码不对"）✅
           分开说"邮箱不存在"/"密码错" = 帮攻击者枚举用户 ✅ ✗
        ⚠️ 但**不存在的用户也要跑一次 bcrypt**（时间对齐 ✅）
           否则响应快慢能泄露"这个邮箱存在" ✅ ✗
        """
        from sqlalchemy import select

        from hub.core.auth import (create_token, hash_password, token_ttl_seconds,
                                   verify_password)
        from hub.persistence.db import get_session
        from hub.persistence.models import AppUser

        email = body.email.strip().lower()
        with get_session() as s:
            row = s.scalars(select(AppUser).where(
                AppUser.email == email, AppUser.status == "ACTIVE")).first()
            if row is not None:
                ok = verify_password(body.password, row.password_hash)
                user = UserOut(id=str(row.id), email=row.email,
                               display_name=row.display_name, role=row.role)
                uid, tenant = str(row.id), row.tenant_id
            else:
                # 时间对齐：用户不存在也做一次 bcrypt ✅
                verify_password(body.password, hash_password("dummy-timing-equalizer"))
                ok, user, uid, tenant = False, None, "", "default"

        if not ok or user is None:
            # 审计：登录失败也要记（`login_failed` ✅）
            from hub.audit import write as audit_write
            audit_write(actor=f"anon:{email}", action="login_failed",
                        resource="user:none", detail={"email": email},
                        ip=request.client.host if request.client else None,
                        trace_id=_trace_id(request))
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                {"code": "BAD_CREDENTIALS",
                                 "message": "邮箱或密码不对",
                                 "trace_id": _trace_id(request)})

        ttl = token_ttl_seconds()
        token = create_token(user_id=uid, email=user.email, role=user.role,
                             tenant_id=tenant, ttl_seconds=ttl)
        # 审计：登录成功 ✅
        from hub.audit import write as audit_write
        audit_write(actor=f"user:{uid}", action="login", resource=f"user:{uid}",
                    detail={"email": user.email, "role": user.role},
                    ip=request.client.host if request.client else None,
                    trace_id=_trace_id(request))
        return LoginOut(access_token=token, expires_in=ttl, user=user)

    @app.post("/api/auth/logout")
    def logout(request: Request, user: AuthUser = Depends(get_current_user)):
        """**服务端登出**：把这个 token 的 jti 记进黑名单 → 立刻失效 ✅

        为什么只作废"当前这个 token"，不作废"这个用户全部 token"：
            用户可能在手机和电脑同时登录 ✅
            一个设备登出不该把另一个踢掉 ✅
            （以后要"全设备登出"，加一个"用户级撤销时间戳"即可 ✅）
        """
        from hub.core.auth import revoke
        from hub.audit import write as audit_write

        auth = request.headers.get("authorization") or ""
        token = auth.split(" ", 1)[1].strip() if " " in auth else ""
        ok = revoke(token) if token else False

        audit_write(actor=f"user:{user.id}", action="logout",
                    resource=f"user:{user.id}",
                    detail={"ok": ok, "email": user.email},
                    ip=request.client.host if request.client else None,
                    trace_id=_trace_id(request))
        if not ok:
            # Redis 挂了 → **诚实报错**，不假装登出成功 ✅
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {"code": "REVOKE_FAILED",
                 "message": "登出没能生效（Redis 连不上）—— token 仍会在有效期内可用",
                 "trace_id": _trace_id(request)})
        return {"ok": True, "message": "已登出，该 token 立即失效"}

    @app.get("/api/auth/me", response_model=UserOut)
    def me(request: Request, user: AuthUser = Depends(get_current_user)):
        """用 token 换回自己的身份（前端刷新页面时用它 ✅）"""
        from sqlalchemy import select

        from hub.persistence.db import get_session
        from hub.persistence.models import AppUser

        with get_session() as s:
            row = s.scalars(select(AppUser).where(
                AppUser.email == user.email)).first()
            if row is None or row.status != "ACTIVE":
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    {"code": "USER_GONE",
                                     "message": "用户不存在或已停用",
                                     "trace_id": _trace_id(request)})
            return UserOut(id=str(row.id), email=row.email,
                           display_name=row.display_name, role=row.role)

    # ---------- 读接口（阶段 4.4a，8 个）----------
    # ⚠️ 放在 `create_app()` **里面** include：否则 `main` 和 `read` 互相 import 会成环 ✅
    from hub.api.read import router as read_router
    app.include_router(read_router)
    # ---------- 图表聚合（阶段 4.4b/4.4c，9 个）----------
    from hub.api.charts import router as charts_router
    app.include_router(charts_router)
    # ---------- 写接口 · 批 5.1（提交与查询，4 个）----------
    from hub.api.write_run import router as write_run_router
    app.include_router(write_run_router)
    # ---------- 写接口 · 批 5.2（审批链，6 个）----------
    from hub.api.write_action import router as write_action_router
    app.include_router(write_action_router)
    # ---------- 写接口 · 批 5.3（通知/知识/导出/死信，5 个）----------
    from hub.api.write_misc import router as write_misc_router
    app.include_router(write_misc_router)
    # ---------- 写接口 · 数据上传（2026-11-01 补的真缺口）----------
    # 原来只能 `hub p0 <文件>` 命令行导入；42 条路由里**没有任何上传入口** ✅ ✗
    from hub.api.images import router as images_router
    from hub.api.write_ingest import router as write_ingest_router
    app.include_router(write_ingest_router)
    # ---------- 图片明细 + 人工复核（2026-11-03 加，新文件 ✅ 零冲突）----------
    app.include_router(images_router)

    # ---------- OpenAPI 元数据（中文标题 / 分组说明 / 响应示例）----------
    # 2026-11-01：`/api/docs` 原来全是英文标题（函数名自动生成）、没有返回结构
    #   → 用一个**后处理器**统一补：改文案不用碰 43 个装饰器 ✅
    # ⚠️ 只改**文档**，不影响任何请求行为 ✅
    from hub.api.openapi_meta import apply_openapi
    _base_openapi = app.openapi

    def _openapi():
        if not app.openapi_schema:
            app.openapi_schema = apply_openapi(_base_openapi())
        return app.openapi_schema

    app.openapi = _openapi

    return app


app = create_app()
