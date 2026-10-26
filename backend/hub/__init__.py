# -*- coding: utf-8 -*-
"""
跨平台电商 AI 运营决策中枢 —— 后端包（hub）
==========================================

这是整个后端的代码根包。所有模块都从这里开始，用 `from hub.xxx import yyy` 引用。

目录结构（当前已完成 core + contracts + persistence，其余待建）：

    hub/
    ├── core/          基础设施：配置、错误、日志、Trace、成本
    ├── contracts/     契约层：所有数据结构定义（模块间通信的唯一标准）
    ├── persistence/   存储层：数据库连接 + 全量 34 张表 + 读写
    ├── ingress/       数据接入：适配器（解析淘宝/京东导出文件 → Record）
    ├── dqe/           M1 数据降噪：规则轨 + ML 轨 + 决策融合
    ├── multimodal/    M2 多模态沙箱：图片阅后即焚
    ├── graph/         M3 归因链：LangGraph 状态机
    ├── analysis/      M4 复合分析：聚类 / 价格背离 / 优先级 / 预测 / CaseRAG
    ├── gateway/       M5 工具网关：注册表 / 四道防线 / 风险分级
    ├── execution/     M6 有界执行链：调用 / 状态回查 / 失败回滚
    ├── scheduler/     M7 调度：Redis Stream 队列
    ├── eval/          M8 评估：30 门禁 / 三层评测 / Judge
    ├── retro/         M9 效果复盘：对照组 DID
    ├── business/      M11 业务数据面：商品 / 销售 / 流量 / 退款
    ├── collab/        M10 协同：回复 / 通知 / 报表 / 权限
    └── pipeline/      编排层：把以上模块串成一条流水线

依赖方向（单向，禁止反向）：
    业务模块 → contracts → core
    （core 不依赖任何模块，contracts 不依赖任何业务模块）

当前进度：
    ✅ core/         配置(config.py) + 错误(errors.py)
    ✅ contracts/    全部契约（ingress/quality/analysis/multimodal/gateway/retro/case）
    ✅ persistence/  存储层（34 张表 + Alembic 迁移）
    ⬜ 其余模块      待建
"""
