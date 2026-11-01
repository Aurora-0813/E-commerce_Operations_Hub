# -*- coding: utf-8 -*-
"""
API 认证测试（check_api_auth.py）—— 阶段 4.1
===========================================
用 **FastAPI TestClient**（不启服务、不占端口）→ 秒级，进 quick 组 ✅

验 9 条：
    [1] 无 token → 401，且**带 trace_id**
    [2] token 格式错 → 401
    [3] 密码错 → 401，且**报错信息不泄露"邮箱是否存在"**
    [4] 不存在的邮箱 → **和密码错同一句话**（防枚举）
    [5] 登录成功 → 有 token + expires_in == 1800（30 分钟 ✅）
    [6] 拿 token 调 /auth/me → 拿到自己的身份
    [7] **token 过期 → 401**（用负数 ttl 造一个已过期的）
    [8] **登录成功/失败都写了审计**（`login` / `login_failed` ✅）
    [9] **伪造签名的 token → 401**（证明密钥真的在起作用）

第 [9] 条为什么重要：
    如果 `decode_token` 忘了校验签名，那**谁都能伪造 admin** ✅ ✗
    这条测的就是"签名有没有被验证" ✅
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
PWD = "123456"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 66)
    print("  API 认证（阶段 4.1）")
    print("=" * 66)

    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 无 token ----------
    print("\n[1] 无 token → 401 且带 trace_id")
    r = c.get("/api/auth/me")
    check("状态码 401", r.status_code == 401, str(r.status_code))
    body = r.json()
    check("有 trace_id", bool(body.get("trace_id")), str(body)[:80])
    check("code=NO_TOKEN", body.get("code") == "NO_TOKEN", str(body.get("code")))

    # ---------- [2] token 格式错 ----------
    print("\n[2] token 乱写 → 401")
    r = c.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    check("状态码 401", r.status_code == 401, str(r.status_code))
    check("code=TOKEN_INVALID", r.json().get("code") == "TOKEN_INVALID",
          str(r.json().get("code")))

    # ---------- [3]/[4] 密码错 vs 邮箱不存在 → **同一句话** ----------
    print("\n[3][4] 密码错 / 邮箱不存在 → 同一句话（防枚举）")
    r1 = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": "wrong"})
    r2 = c.post("/api/auth/login", json={"email": "nobody@hub.local", "password": "wrong"})
    check("密码错 401", r1.status_code == 401, str(r1.status_code))
    check("邮箱不存在 401", r2.status_code == 401, str(r2.status_code))
    check("两句话完全一样", r1.json().get("message") == r2.json().get("message"),
          f"{r1.json().get('message')} / {r2.json().get('message')}")

    # ---------- [5] 登录成功 ----------
    print("\n[5] 登录成功")
    r = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": PWD})
    check("200", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("有 access_token", bool(data.get("access_token")))
    # ⚠️ **不写死秒数**（2026-10-31 改：30 分钟 → 3 小时 ✅）
    #    读配置来比 → 以后改 `.env` 这里的测试不会假红 ✅
    from hub.core.auth import token_ttl_seconds
    check(f"expires_in == 配置值（{token_ttl_seconds()} 秒）",
          data.get("expires_in") == token_ttl_seconds(), str(data.get("expires_in")))
    check("role=operator", (data.get("user") or {}).get("role") == "operator")
    token = data["access_token"]

    # ---------- [6] /me ----------
    print("\n[6] 用 token 换身份")
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    check("200", r.status_code == 200, str(r.status_code))
    check("email 对得上", r.json().get("email") == "operator@hub.local",
          r.json().get("email"))
    check("display_name 有值", bool(r.json().get("display_name")),
          r.json().get("display_name"))

    # ---------- [7] 过期 token ----------
    print("\n[7] 过期 token → 401（且说明是过期）")
    from hub.core.auth import create_token
    old = create_token(user_id="x", email="a@b.c", role="operator", ttl_seconds=-10)
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})
    check("401", r.status_code == 401, str(r.status_code))
    check("message 提到过期", "过期" in str(r.json().get("message")),
          str(r.json().get("message"))[:40])

    # ---------- [8] 审计 ----------
    print("\n[8] 登录成功/失败都写了审计")
    from sqlalchemy import delete, func, select
    from hub.persistence.db import get_session
    from hub.persistence.models import AuditLog
    with get_session() as s:
        n_ok = s.scalar(select(func.count()).select_from(AuditLog)
                        .where(AuditLog.action == "login")) or 0
        n_bad = s.scalar(select(func.count()).select_from(AuditLog)
                         .where(AuditLog.action == "login_failed")) or 0
    check("有 login 记录", n_ok > 0, f"{n_ok} 条")
    check("有 login_failed 记录", n_bad > 0, f"{n_bad} 条")
    # 清掉测试造的审计
    with get_session() as s:
        s.execute(delete(AuditLog).where(AuditLog.action.in_(("login", "login_failed"))))
        s.commit()

    # ---------- [9] 伪造签名 ----------
    print("\n[9] 伪造签名的 token → 401（证明签名真的在验）")
    import jwt as _jwt
    forged = _jwt.encode({"sub": "hacker", "role": "admin", "typ": "access",
                          "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    check("401（签名不对就不认）", r.status_code == 401, str(r.status_code))

    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
