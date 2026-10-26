# -*- coding: utf-8 -*-
"""
基础设施层（core）
=================

放全项目最底层、被所有模块依赖的东西。它们**不依赖任何业务模块**，
保证依赖方向单向（不会循环依赖）。

当前包含：
    ├── config.py   全局配置：pydantic-settings，从环境变量/.env 读
    │               优先级：环境变量 > .env > 代码默认值
    └── errors.py   统一错误：HubError（带 code/message/retryable/detail）
                    + ErrorCode 错误码表（CPP-1xxx~6xxx 分段管理）

以后会加（对应开发指南 02 的 B2）：
    ├── logging.py  结构化日志（JSON，含 run_id/step/impl）
    ├── tracing.py  链路追踪（span 落 trace_span 表）
    └── cost.py     模型成本记账（token → 金额）

为什么这些放最底层：
    配置、错误、日志、追踪是"每个模块都要用"的东西，
    它们不依赖业务，所以放最底层，保证"模块 → core"单向依赖。
"""
