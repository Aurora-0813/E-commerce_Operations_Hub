# -*- coding: utf-8 -*-
"""
Alembic 迁移环境（env.py）
=========================
Alembic 运行时读这个文件。它做两件事：
    1. 从 Settings（.env）读出数据库 URL；
    2. 把 models.py 里的 Base.metadata 交给 Alembic，用于 autogenerate。
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from hub.core.config import Settings
from hub.persistence.models import Base

# Alembic 的配置对象（读 alembic.ini）
config = context.config

# 配置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从 Settings 读数据库 URL（.env 里的 HUB_STORAGE__URL）
config.set_main_option("sqlalchemy.url", Settings().storage.url)

# 这是 autogenerate 的"比对基准"：我们所有表的元数据
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连数据库，执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
