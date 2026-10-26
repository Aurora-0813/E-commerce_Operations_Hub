# -*- coding: utf-8 -*-
"""
数据降噪引擎（DQE）
==================
对应开发指南 04：把导入的评价判定为 keep / review / drop。

两条轨道 + 一个融合：
    ├── normalize.py  文本归一化（基础）
    ├── simhash.py    近似重复检测（R007）
    ├── rules.py      9 条硬规则（R001–R009）
    ├── ml.py         水军分类器（TF-IDF + 逻辑回归）
    ├── fusion.py     决策融合（规则优先 + 差评保护）
    └── engine.py     主流程（读 record → 判定 → 回填）

核心原则：
    1. 规则轨永远可用（模型挂了也能跑，降级 rule_only）；
    2. 差评（rating ≤ 2）不静默丢弃，降级为 review；
    3. 唯一硬 drop 是 R008（Prompt 注入）。
"""
