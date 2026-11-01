# -*- coding: utf-8 -*-
"""
建初始用户（seed_users.py）—— 阶段 4.1
======================================
路线图 §10.1：**不做用户管理界面，初期用 SQL/脚本插入** ✅

建两个用户（对照路线图"初始两用户"）：

    operator@hub.local    role=operator    运营（能提 L2，不能批 L3）
    supervisor@hub.local  role=supervisor  主管（能批 L3 改价）

密码来自 `.env` 的 `HUB_AUTH__SEED_PASSWORD`，**没配就用 `123456`**（你定的 ✅）

**为什么密码要能配**：
    硬编码 `123456` 进代码 = 生产上线还是 `123456` ✅ ✗
    放进 `.env`（已在 .gitignore）→ 换环境换密码，代码不动 ✅

**幂等**：重复跑只更新密码，不新建用户 ✅
（`app_user` 有 UNIQUE(tenant_id, email) ✅）

跑法：
    python -m scripts.seed_users
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许 `python -m scripts.seed_users` 从 backend/ 跑 ✅
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                       # noqa: E402

from hub.core.auth import hash_password             # noqa: E402
from hub.persistence.db import get_session          # noqa: E402
from hub.persistence.models import AppUser, Tenant  # noqa: E402

TENANT_ID = "default"
TENANT_NAME = "默认租户"

# (email, 显示名, role) —— role 必须在 app_user 的 CHECK 里 ✅
#   operator/supervisor/admin/analyst —— **没有 manager** ✅
SEED_USERS = [
    ("operator@hub.local", "运营小李", "operator"),
    ("supervisor@hub.local", "主管老王", "supervisor"),
]


def seed(password: str | None = None) -> list[str]:
    """建/更新初始用户。返回一句人话的结果。"""
    if password is None:
        from hub.core.config import Settings
        s = Settings()
        password = getattr(getattr(s, "auth", None), "seed_password", "") or "123456"

    out: list[str] = []
    pwd_hash = hash_password(password)        # bcrypt 慢（~250ms）→ 只算一次 ✅

    with get_session() as s:
        # ---- 租户（AppUser.tenant_id 是真实外键，必须先有 ✅）----
        if s.get(Tenant, TENANT_ID) is None:
            s.add(Tenant(id=TENANT_ID, name=TENANT_NAME))
            s.flush()
            out.append(f"新建租户 {TENANT_ID}")
        else:
            out.append(f"租户已存在 {TENANT_ID}")

        # ---- 用户（幂等：有就更新密码/角色 ✅）----
        for email, name, role in SEED_USERS:
            row = s.scalars(select(AppUser).where(
                AppUser.tenant_id == TENANT_ID, AppUser.email == email)).first()
            if row is None:
                s.add(AppUser(tenant_id=TENANT_ID, email=email, display_name=name,
                              role=role, password_hash=pwd_hash, status="ACTIVE"))
                out.append(f"新建用户 {email}（{role}）")
            else:
                row.password_hash = pwd_hash
                row.role = role
                row.display_name = name
                row.status = "ACTIVE"
                out.append(f"更新用户 {email}（{role}）")

        s.commit()

    # 审计：建用户（`user.create`）—— 路线图 §10.2 要求记 ✅
    from hub.audit import write as audit_write
    for email, name, role in SEED_USERS:
        audit_write(actor="system:seed_users", action="user.create",
                    resource=f"user:{email}",
                    detail={"display_name": name, "role": role})
    return out


def main() -> int:
    print("=" * 60)
    print("  建初始用户（阶段 4.1）")
    print("=" * 60)
    try:
        for line in seed():
            print("  ✅ " + line)
    except Exception as e:                      # noqa: BLE001
        print(f"  ❌ 失败：{type(e).__name__}: {e}")
        return 1
    print("\n  登录用：operator@hub.local / supervisor@hub.local")
    print("  密码：HUB_AUTH__SEED_PASSWORD（没配则 123456）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
