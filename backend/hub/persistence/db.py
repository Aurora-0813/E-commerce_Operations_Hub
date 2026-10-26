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
engine = create_engine(_settings.storage.url, echo=False, future=True)

# 会话工厂：每次操作数据库时创建一次会话
SessionLocal = sessionmaker(bind=engine, future=True)


def get_session() -> Session:
    """取一个数据库会话。

    用法：
        with get_session() as s:
            s.add(obj)
            s.commit()
    """
    return SessionLocal()
