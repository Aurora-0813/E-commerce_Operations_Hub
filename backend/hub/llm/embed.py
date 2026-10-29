# -*- coding: utf-8 -*-
"""
Ollama embedding 客户端（embed.py）
==================================
调 Ollama 的 `/api/embed` 拿句子向量。

为什么用 Ollama 而不是 sentence-transformers：
    sentence-transformers 依赖 PyTorch（装完 1~2GB），
    而 Ollama 本来就在跑（M2/M3 都在用），换个模型只是 `ollama pull` 的事——
    **零新依赖**。

常用中文/多语模型：
    bge-m3                 1024 维，多语言，中文效果好（~1.2GB）
    qwen3-embedding:0.6b   1024 维，轻量
    nomic-embed-text       768 维，偏英文

和 client.py 的关系：
    client.py 走 `/api/chat`（生成），这里走 `/api/embed`（向量），
    传输层都是标准库 urllib，风格一致。
"""
import json
import urllib.error
import urllib.request

import numpy as np


class OllamaEmbed:
    """本地 Ollama embedding 模型。"""

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "bge-m3", timeout: int = 120,
                 batch_size: int = 32):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.name = model

    def embed(self, texts: list[str]) -> np.ndarray:
        """把一批文本转成向量矩阵（n × dim）。

        :param texts: 文本列表
        :return: np.ndarray，形状 (len(texts), dim)
        :raises RuntimeError: 连接失败 / 模型没拉 / 返回维度不一致
        """
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start:start + self.batch_size]
            raw = self._post(f"{self.base_url}/api/embed",
                             {"model": self.model, "input": chunk})
            batch = raw.get("embeddings")
            if not batch:
                raise RuntimeError(
                    f"Ollama embedding 返回为空（model={self.model}）。"
                    f"先跑 `ollama pull {self.model}`"
                )
            vectors.extend(batch)

        arr = np.asarray(vectors, dtype=np.float32)
        if arr.shape[0] != len(texts):
            raise RuntimeError(
                f"向量条数不对：要 {len(texts)} 条，拿到 {arr.shape[0]} 条"
            )
        return arr

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:                    # noqa: BLE001
                pass
            raise RuntimeError(
                f"Ollama embedding 调用失败（{e.code}）：{body}\n"
                f"如果是 404，说明模型没拉：`ollama pull {self.model}`"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"连不上 Ollama（{url}）：{e}。请确认 `ollama serve` 在跑。"
            ) from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Ollama 返回的不是 JSON：{e}") from e


def get_embedder() -> OllamaEmbed:
    """按 .env 配置取 embedding 模型（没配就用 bge-m3 + 默认地址）。"""
    from hub.core.config import Settings

    s = Settings()
    return OllamaEmbed(
        base_url=s.llm.base_url or "http://localhost:11434",
        model="bge-m3",
        timeout=s.llm.timeout_s or 120,
    )
