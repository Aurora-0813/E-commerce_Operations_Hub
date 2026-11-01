# -*- coding: utf-8 -*-
"""
认证核心（auth.py）—— 阶段 4.1
==============================
三件事，只做这三件：

    password_hash  /  verify_password   —— bcrypt
    create_token   /  decode_token      —— JWT（HS256，默认 30 分钟）

**为什么单独一个模块、不写进 API 层**：
    CLI 也要能造用户、能验 token ✅（`python -m hub.scripts.seed_users`）
    而且 API 层的中间件只该做"解析 + 注入"，不该管密码算法 ✅

**JWT secret 的处理**（2026-10-31 你的决定"给个默认值"）：
    `.env` 没配时用一个**显式的开发默认值**，并在启动时**打印警告** ✅
    为什么不能"没配就直接崩"：本地开发每次都要配一遍，太烦 ✅
    为什么必须警告：默认值意味着**任何人都能签 token** → 上生产前必须换 ✅
    （比"静默用默认值"安全的地方在于：它每次都喊 ✅）

**token 有效期 30 分钟**（你定的）：
    为什么短：JWT 一旦发出，服务端无法撤销 ✅
    → 有效期越短，泄露的窗口越小 ✅
    → 代价是用户每半小时要重新登录；先按 30 分钟做，以后要"刷新"再加 ✅
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

# ⚠️ 开发默认密钥（.env 没配时用它，启动时打警告 ✅）
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"
ALGORITHM = "HS256"
DEFAULT_MINUTES = 30

_SECRET_WARNED = False


def _secret() -> str:
    """取 JWT 密钥。优先 `.env` 的 `HUB_AUTH__JWT_SECRET` ✅"""
    global _SECRET_WARNED
    from hub.core.config import Settings
    s = Settings()
    secret = getattr(getattr(s, "auth", None), "jwt_secret", "") or ""
    if not secret:
        secret = DEV_JWT_SECRET
        if not _SECRET_WARNED:
            _SECRET_WARNED = True
            print("=" * 70)
            print("  ⚠️ 没配 HUB_AUTH__JWT_SECRET，正在用**公开的开发默认密钥**")
            print("     这意味着**任何人都能伪造 token** —— 上生产前必须配一个随机串")
            print("     生成方法：python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
            print("=" * 70)
    return secret


def token_ttl_seconds() -> int:
    """token 有效期（秒）。默认 30 分钟 ✅"""
    from hub.core.config import Settings
    s = Settings()
    minutes = getattr(getattr(s, "auth", None), "access_token_minutes", DEFAULT_MINUTES)
    return int(minutes) * 60


# ════════════════════════════════════════════════════ 密码
def _sha256(password: str) -> str:
    """bcrypt 的输入上限 72 字节 —— 先 sha256 再交给 bcrypt。

    **为什么要这一步**：
        bcrypt **会静默截断**超过 72 字节的密码 ✅
        → 两个前 72 字节相同的密码会被认为相同 ✅ ✗
        先 sha256（定长 32 字节）就没有这个问题 ✅
    ⚠️ 这是**哈希前的规整**，不是"自己实现密码哈希" ✅
       （盐、cost 仍然由 bcrypt 负责 ✅）
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """bcrypt 哈希（cost=12，约 250ms —— 故意慢，抗暴力猜）✅"""
    import bcrypt
    if not password:
        raise ValueError("密码不能为空")
    return bcrypt.hashpw(_sha256(password).encode("ascii"),
                         bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """验密码。**哈希为空 = 这个用户不能登录**（比如只走 SSO 的账号）✅"""
    import bcrypt
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_sha256(password).encode("ascii"),
                              password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # 哈希格式坏了 → 当作不匹配（**不要抛异常**，否则能靠报错探测用户 ✅）
        return False


# ════════════════════════════════════════════════════ JWT
def create_token(*, user_id: str, email: str, role: str,
                 tenant_id: str = "default",
                 ttl_seconds: int | None = None) -> str:
    """签发 JWT。payload 里**只放身份，不放权限明细** ✅

    为什么只放身份：
        权限（谁能批 L2/L3）由 `ToolSpec.required_role` 决定 ✅
        如果把权限写进 token，改权限就得等 token 过期 ✅ ✗
    """
    import jwt

    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else token_ttl_seconds()
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "tenant": tenant_id,
        "iat": now,
        "exp": now + ttl,
        # `jti` = 这个 token 的唯一 id ✅
        # **服务端撤销靠它**：登出时把 jti 记进黑名单，校验时查一下 ✅
        # （没有 jti 就只能拿"整个 token"当黑名单 key，太长 ✅）
        "jti": uuid.uuid4().hex,
        # 用途声明：以后要加"刷新 token"时，靠它区分 ✅
        "typ": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


# ════════════════════════════════════════════════════ 服务端撤销（登出）
# 用 **Redis** 而不是数据库表：
#   ① 黑名单是**短期**数据（token 一过期就没用了）→ Redis 的 TTL 天然适合 ✅
#   ② 不需要新建表/迁移 ✅
#   ③ Redis 已经是队列的硬依赖 ✅
_BLACKLIST_PREFIX = "hub:jwt:revoked:"


def revoke(token: str) -> bool:
    """**服务端登出**：把这个 token 的 `jti` 记进黑名单，直到它原本到期为止 ✅

    TTL 设成"剩余有效期"而不是固定值：过期后 jti 自然消失，**黑名单不会无限增长** ✅
    :return: True = 已撤销；False = Redis 连不上（**调用方应当报错，而不是假装成功** ✅）
    """
    try:
        payload = decode_token(token, check_revoked=False)
    except TokenInvalid:
        return True           # 已经无效的 token，等效于已撤销 ✅

    remain = int(payload.get("exp", 0)) - int(time.time())
    if remain <= 0:
        return True           # 已经过期 ✅

    try:
        from hub.queue.client import get_redis
        get_redis().setex(_BLACKLIST_PREFIX + str(payload["jti"]), remain, "1")
        return True
    except Exception:                             # noqa: BLE001
        return False


def is_revoked(jti: str) -> bool:
    """这个 jti 被撤销了吗。

    ⚠️ **Redis 连不上时返回 False（放行）** —— 这是有意的取舍 ✅
       理由：登出是"降低风险"，不是"阻止危险操作" ✅
       Redis 挂了就把所有人踢下线，比"撤销暂时失效"更糟 ✅
       代价：Redis 挂的期间，已登出的 token 仍能用（最长到 3 小时后过期）✅
       **写出来**，否则以后有人以为撤销是硬保证 ✅
    """
    try:
        from hub.queue.client import get_redis
        return bool(get_redis().exists(_BLACKLIST_PREFIX + str(jti)))
    except Exception:                             # noqa: BLE001
        return False


class TokenInvalid(Exception):
    """token 无效/过期。**调用方转 401** ✅"""


def decode_token(token: str, *, check_revoked: bool = True) -> dict:
    """解析并校验 JWT。失败抛 `TokenInvalid`（**带人话原因** ✅）

    `check_revoked=True`（默认）会查黑名单 → **登出过的 token 立刻失效** ✅
    """
    import jwt

    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenInvalid("token 已过期（请重新登录）") from None
    except jwt.InvalidTokenError as e:
        raise TokenInvalid(f"token 无效：{e}") from None

    if payload.get("typ") != "access":
        raise TokenInvalid("token 类型不对（不是 access）")
    for key in ("sub", "role"):
        if not payload.get(key):
            raise TokenInvalid(f"token 缺少字段：{key}")

    # **服务端撤销检查**：登出过的 token 立即失效 ✅
    if check_revoked and payload.get("jti") and is_revoked(str(payload["jti"])):
        raise TokenInvalid("token 已失效（你已登出，请重新登录）")
    return payload


# ════════════════════════════════════════════════════ 小工具
def constant_time_eq(a: str, b: str) -> bool:
    """常数时间比较（避免时序侧信道）。给"比较 key/token"这类场景用 ✅"""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
