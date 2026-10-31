# -*- coding: utf-8 -*-
"""
LLM 包（hub/llm）
=================
文本 LLM 的统一入口。

和 hub/multimodal 的关系：
    multimodal/vision.py  —— 图片 → 结构化标签（VLM，**只连本地**，图片不出网）
    llm/client.py         —— 文本 → 文本（OpenAI 兼容，可切本地/云端）

为什么要分两个协议（2026-10-31，阶段 1.1）：
    文本走 OpenAI 兼容协议 → 换模型不改代码 ✅
    视觉走 Ollama 原生协议且只连本地 → 图片永不出网 ✅
    （文本可以上云是因为"只有聚合可上云"这条规则 —— 见 docs/改进路线图.md §二）
"""
from hub.llm.client import OpenAIText, TextLLM, get_text_llm

__all__ = ["TextLLM", "OpenAIText", "get_text_llm"]
