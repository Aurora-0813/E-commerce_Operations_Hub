# -*- coding: utf-8 -*-
"""
环境预检（check_env.py）
=======================
在跑 Alembic 之前，先确认：依赖、配置、模型、数据库连通 都没问题。

运行（在 backend/ 目录下）：
    python tests/check_env.py
"""
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path：这样从任何目录（终端/IDE）运行，都能 import hub
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main() -> None:
    ok = True

    # 1. 依赖检查
    print("=== 1. 依赖检查 ===")
    for mod in ["sqlalchemy", "alembic", "psycopg"]:
        try:
            __import__(mod)
            print(f"✅ {mod}")
        except ImportError:
            print(f"❌ {mod} 未安装（pip install {mod}）")
            ok = False

    # 2. 配置检查
    print("\n=== 2. 配置检查 ===")
    from hub.core.config import _ENV_FILE, Settings
    print(f".env 路径 = {_ENV_FILE}")
    print(f".env 存在 = {_ENV_FILE.exists()}")
    s = Settings()
    print(f"storage.url = {s.storage.url}")
    if s.storage.url.startswith("sqlite"):
        print("⚠️  警告：当前读到的是 SQLite！")
        print("    检查上面的 .env 路径是否存在、内容是否为 HUB_STORAGE__URL=...")
        ok = False
    else:
        print("✅ 读到 PostgreSQL 连接")

    # 3. 模型检查
    print("\n=== 3. 模型检查 ===")
    from hub.persistence.models import Base
    tables = sorted(Base.metadata.tables)
    print(f"共 {len(tables)} 张表：")
    for name in tables:
        print(f"   - {name}")
    if len(tables) != 34:
        print(f"⚠️  预期 34 张，实际 {len(tables)} 张")

    # 4. 数据库连通检查
    print("\n=== 4. 数据库连通检查 ===")
    try:
        from sqlalchemy import text

        from hub.persistence.db import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ 数据库连接成功")
    except Exception as e:
        print(f"❌ 连接失败：{e}")
        print("    常见原因：数据库没建 / 密码错 / PostgreSQL 服务没启动")
        ok = False

    print("\n" + ("✅ 预检通过，可以跑 alembic。" if ok else "❌ 预检未通过，先修上面标红的问题。"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
