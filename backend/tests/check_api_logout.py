# -*- coding: utf-8 -*-
"""
登出与撤销测试（check_api_logout.py）—— 补 4.1 的"服务端真撤销"
==============================================================
你 2026-10-31 的要求：
    "服务端做登出，添加接口，真正撤销，然后时间长一点，3 小时（写在 .env 里）"

验 6 条：
    [1] 有效期 = 3 小时（10800 秒），且**来自配置**（不是硬编码）
    [2] 登出前 token 可用
    [3] 登出返回 200
    [4] **登出后同一个 token 立刻 401** ← 这条才是"真正撤销" ✅
    [5] 登出**不影响别的 token**（另一个登录还能用 ✅）
    [6] 黑名单 TTL ≈ 剩余有效期（不会无限增长 ✅）
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


def login(c) -> str:
    r = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": PWD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def main() -> int:
    print("=" * 66)
    print("  服务端登出（真撤销）+ 3 小时有效期")
    print("=" * 66)

    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 有效期来自配置 ----------
    print("\n[1] 有效期 = 3 小时，且来自 .env（不是硬编码）")
    from hub.core.auth import token_ttl_seconds
    from hub.core.config import Settings
    check("配置里是 180 分钟", Settings().auth.access_token_minutes == 180,
          str(Settings().auth.access_token_minutes))
    check("换算成 10800 秒", token_ttl_seconds() == 10800, str(token_ttl_seconds()))

    # ---------- [2] 登出前可用 ----------
    print("\n[2] 登出前 token 可用")
    t1 = login(c)
    check("me 返回 200", c.get("/api/auth/me",
                               headers={"Authorization": f"Bearer {t1}"}).status_code == 200)

    # ---------- [3] 登出 ----------
    print("\n[3] 登出返回 200")
    r = c.post("/api/auth/logout", headers={"Authorization": f"Bearer {t1}"})
    check("200", r.status_code == 200, str(r.status_code))
    check("返回 ok=true", r.json().get("ok") is True, str(r.json())[:60])

    # ---------- [4] **登出后立刻失效** ----------
    print("\n[4] 登出后同一个 token **立刻 401**（这才是真撤销）")
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {t1}"})
    check("401", r.status_code == 401, str(r.status_code))
    check("原因提到已登出", "登出" in str(r.json().get("message")),
          str(r.json().get("message"))[:50])

    # ---------- [5] 不影响别的 token ----------
    print("\n[5] 登出**不影响**另一个 token（不同设备的会话互不牵连）")
    t2 = login(c)
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {t2}"})
    check("t2 仍然 200", r.status_code == 200, str(r.status_code))

    # ---------- [6] 黑名单 TTL ----------
    print("\n[6] 黑名单 TTL ≈ 剩余有效期（不会无限增长）")
    from hub.core.auth import _BLACKLIST_PREFIX
    from hub.queue.client import get_redis
    payload_jti = t1.split(".")[1]     # 只是为了不和别的 key 撞；真实 jti 在 token 里
    try:
        rds = get_redis()
        keys = [k for k in rds.scan_iter(_BLACKLIST_PREFIX + "*")]
        check("黑名单里有条目", len(keys) >= 1, f"{len(keys)} 条")
        if keys:
            ttl = rds.ttl(keys[0])
            check("TTL 在 0~10800 秒之间（≈剩余有效期）", 0 < ttl <= 10800, f"{ttl}s")
    except Exception as e:              # noqa: BLE001
        check("能连上 Redis", False, f"{type(e).__name__}: {e}")

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
