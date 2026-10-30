# -*- coding: utf-8 -*-
"""
LLM 文本客户端（client.py）
==========================
调用本地 Ollama 做**纯文本**推理（不带 images）。

为什么单独一层：
    1. M3 的分类/归因都是文本任务，不该复用 vision.py（它带图片 payload）；
    2. 将来换云端（通义/智谱），只改这一层，上层代码不动；
    3. 客户端只管"发请求 + 拿文本"，重试/降级由上层决定。

依赖：只用标准库 urllib（和 vision.py 一致），不引入 openai SDK。
"""
import json
import urllib.error
import urllib.request
from typing import Protocol


class TextLLM(Protocol):
    """文本 LLM 的协议（接口约定）。"""
    name: str

    def chat(self, prompt: str, *, system: str | None = None,
             temperature: float = 0.0, max_tokens: int | None = None) -> str:
        ...


class OllamaText:
    """本地 Ollama 文本模型（默认 Qwen2.5-VL-3B 的文本模式）。

    Qwen2.5-VL 是多模态模型，去掉 images 字段就是纯文本模式——
    复用已下载的模型，不用再拉一个文本模型。
    """

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5vl:3b", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = model

    def chat(self, prompt: str, *, system: str | None = None,
             temperature: float = 0.0, max_tokens: int | None = None) -> str:
        """发一条对话，返回模型输出的纯文本。

        **每次调用都会记一条 span**（`llm.chat`），并写入真实的 token 数
        （Ollama 返回里的 prompt_eval_count / eval_count）。

        为什么 token 这么重要：M3 一次跑 57 次 LLM 调用，
        没有 token 数据就只能"感觉一下贵不贵"；有了它才知道钱花在哪。

        :param prompt:      用户消息
        :param system:      系统提示词（可空）
        :param temperature: 温度（0 = 尽量确定性，分类任务必须用 0）
        :param max_tokens:  最大输出 token 数（可空）
        :raises RuntimeError: 连接失败 / HTTP 错误 / 返回为空
        """
        from hub.core.tracing import span

        with span("llm.chat", kind="llm", model=self.model,
                  prompt_chars=len(prompt)) as sp:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            options: dict = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens

            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": options,
            }

            raw = self._post(f"{self.base_url}/api/chat", payload)
            # Ollama 的返回带 token 统计：本地模型成本≈电费，但 token 是真实数字
            sp.add_tokens(raw.get("prompt_eval_count"), raw.get("eval_count"))
            content = (raw.get("message") or {}).get("content", "")
            if not content.strip():
                raise RuntimeError(f"LLM 返回为空（model={self.model}）")
            sp.add(answer_chars=len(content))
            return content

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"连不上 Ollama（{url}）：{e}。请确认 `ollama serve` 在跑。"
            ) from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Ollama 返回的不是 JSON：{e}") from e


def get_text_llm() -> OllamaText:
    """按 .env 配置取文本 LLM。

    `.env` 里配的是 `HUB_LLM__MODEL`（文本专用，建议 qwen2.5:3b 这类纯文本模型）。

    **兜底为什么是 VLM**：没配时回落到 `qwen2.5vl:3b`（VLM 的文本模式），
    这样**新克隆只需要拉一个模型**（M2 本来就要用 VLM），门槛最低。
    代价是文本效果差一点——配了 `HUB_LLM__MODEL` 就会优先用纯文本模型。
    """
    from hub.core.config import Settings

    s = Settings()
    return OllamaText(
        base_url=s.llm.base_url or "http://localhost:11434",
        model=s.llm.model or "qwen2.5vl:3b",
        timeout=s.llm.timeout_s or 120,
    )
