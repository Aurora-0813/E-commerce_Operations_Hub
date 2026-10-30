# -*- coding: utf-8 -*-
"""
数据库连接（db.py）
==================
用 SQLAlchemy + PostgreSQL。

建表方式：**用 Alembic 迁移**（不再用 create_all）：
    alembic revision --autogenerate -m "initial"   # 从 models.py 生成迁移
    alembic upgrade head                           # 执行迁移，建表

为什么不用 create_all：
    create_all 只建表、不记录版本，改表只能 drop 重建（数据丢）。
    Alembic 记录每次变更，改表加一个 migration 即可，不丢数据。

连接地址从 config 读（.env 里的 HUB_STORAGE__URL）。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from hub.core.config import Settings

# 读配置（默认 sqlite，.env 里覆盖为 PostgreSQL）
_settings = Settings()

# 引擎：真正连数据库的"发动机"
#
# `connect_timeout=5`：**不要让 Windows 的 TCP 超时决定你等多久**。
#   数据库没起来时，TCP 连接不会被"拒绝"（秒报错），而是被"丢弃"，
#   Windows 默认要重试 2 分钟以上才放弃——表现就是"脚本卡住不动"。
#   设了 5 秒，连不上就立刻抛 OperationalError，一眼看出是环境问题。
_url = _settings.storage.url
_engine_kwargs: dict = {"echo": False, "future": True}
if _url.startswith("postgresql"):
    _engine_kwargs["connect_args"] = {"connect_timeout": 5}

engine = create_engine(_url, **_engine_kwargs)

# 会话工厂：每次操作数据库时创建一次会话
SessionLocal = sessionmaker(bind=engine, future=True)


def ping() -> tuple[bool, str]:
    """探活：连一下数据库，返回 (是否可用, 说明)。

    用途：`python -m hub.cli doctor`、以及脚本开头自检——
    "连不上数据库"应该在 5 秒内说清楚，而不是让人等 5 分钟。
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "数据库连接正常"
    except Exception as e:                      # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def get_session() -> Session:
    """取一个数据库会话。

    用法：
        with get_session() as s:
            s.add(obj)
            s.commit()
    """
    return SessionLocal()
