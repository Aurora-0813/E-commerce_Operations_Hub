# -*- coding: utf-8 -*-
"""
agent 包（hub/agent）
=====================
形态 ③ 的受限 agent：**步骤与参数由代码给，模型只做一次判断**。

    handle.py   主路径（手写 4 步）—— 默认，简单可调试
    (待补) graph.py   LangGraph 版对照 —— 同一任务的框架实现，用于对比

为什么主路径是手写而不是 LangGraph：
    形态 ③ 的流程是**固定 4 步、无分支、无循环**，LangGraph 的价值（路由/环/状态机）
    在这里用不上 ✅ 用它包一个固定序列会多一层抽象、更难调试 ✅
    —— 具体取舍见 `docs/开发指南/14_Agent规划能力实测.md` 和路线图 §阶段 3
"""
from hub.agent.handle import HandleResult, Step, handle, save

__all__ = ["HandleResult", "Step", "handle", "save"]
