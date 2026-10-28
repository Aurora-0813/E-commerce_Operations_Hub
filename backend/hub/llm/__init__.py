# -*- coding: utf-8 -*-
"""
LLM 包（hub/llm）
=================
文本 LLM 的统一入口。

和 hub/multimodal 的关系：
    multimodal/vision.py  —— 图片 → 结构化标签（VLM）
    llm/client.py         —— 文本 → 文本（纯文本 LLM，本包）

两者都用本地 Ollama，但职责不同、可以独立替换。
"""
from hub.llm.client import OllamaText, TextLLM, get_text_llm

__all__ = ["TextLLM", "OllamaText", "get_text_llm"]
