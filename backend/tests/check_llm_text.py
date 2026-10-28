# -*- coding: utf-8 -*-
"""
验证 LLM 文本客户端（复用本地 Ollama，不带图片）。

测三件事：
    1. 连得上吗（Ollama 在跑吗）
    2. 能输出结构化 JSON 吗（分类/归因都要）
    3. system 角色生效吗

运行（在 backend/ 目录下）：
    python tests/check_llm_text.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.llm import get_text_llm   # noqa: E402


def main() -> None:
    llm = get_text_llm()
    print(f"模型: {llm.name}｜地址: {llm.base_url}｜超时: {llm.timeout}s\n")

    # ---- 1. 基础对话 ----
    t0 = time.time()
    answer = llm.chat("用一句话解释什么是电商差评。", max_tokens=80)
    print(f"[1] 基础对话（{time.time() - t0:.1f}s）")
    print(f"    → {answer.strip()}\n")

    # ---- 2. 结构化 JSON ----
    prompt = """判断下面这条评价的主题，只输出一行 JSON，不要解释：
{"topic": "quality 或 size 或 logistics 或 other"}

评价：「衣服开线了，穿一次就破」"""
    t0 = time.time()
    out = llm.chat(prompt, max_tokens=60)
    print(f"[2] 结构化输出（{time.time() - t0:.1f}s）")
    print(f"    → {out.strip()}\n")

    # ---- 3. system 角色 ----
    t0 = time.time()
    out = llm.chat("1+1=?", system="你是算术助手，只回答一个数字，不要别的。",
                   max_tokens=10)
    print(f"[3] system 角色（{time.time() - t0:.1f}s）")
    print(f"    → {out.strip()}\n")

    print("（如果 [2] 输出了合法的 JSON、[3] 只返回数字，说明客户端可用）")


if __name__ == "__main__":
    main()
