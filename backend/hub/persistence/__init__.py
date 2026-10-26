# -*- coding: utf-8 -*-
"""
存储层（persistence）
====================

负责"数据落库"：数据库连接、表定义、读写操作。

当前内容：
    ├── db.py          引擎 + 会话（建表用 Alembic，不在这里）
    ├── models.py      全量 34 张表（SQLAlchemy 2.0 声明式）
    └── repository.py  RunRepo（分析运行的增删查改）

建表方式（Alembic）：
    alembic revision --autogenerate -m "initial"   # 从 models.py 生成迁移
    alembic upgrade head                           # 执行迁移建表
"""
