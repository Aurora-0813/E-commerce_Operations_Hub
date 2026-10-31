# -*- coding: utf-8 -*-
"""
LLM 文本客户端（client.py）
==========================
调用**OpenAI 兼容**的文本模型接口，本地 / 云端同一套代码。

为什么改成 OpenAI 兼容协议（2026-10-31，阶段 1.1）：
    原来打的是 Ollama 原生 `/api/chat`，换模型（尤其换云端）就得改代码。
    OpenAI 兼容协议是事实标准：本地 Ollama（`/v1`，0.1.24+ 支持）、
    DeepSeek、通义、智谱、vLLM、LM Studio 全部支持。
    → **同一套代码，改 base_url / api_key / model 就能切换** ✅

    实测差异（两个协议）：
        /api/chat              → {"message": {"content": ...}, "prompt_eval_count": N, ...}
        /v1/chat/completions   → {"choices": [{"message": {"content": ...}}],
                                  "usage": {"prompt_tokens": N, "completion_tokens": M}}
    所以解析和 token 记账都要改 —— 而且必须用现有 20 条 eval 回归一遍。

视觉模型**不走这里**：图片不出网（`docs/改进路线图.md` §二 三条铁律），
`hub/multimodal/vision.py` 继续用 Ollama 原生协议 + 只连本地。
"""
import json
import urllib.error
import urllib.request
from typing import Protocol

from hub.core.cloud_guard import KIND_AGGREGATE, KIND_TEXT, CloudBlocked  # noqa: F401

# 固定随机种子：temperature=0 时默认带上，让"同输入同输出"尽量成立。
# 42 是项目里到处用的种子（数据生成、bootstrap 都用它），保持一致便于追溯。
DEFAULT_SEED = 42


class TextLLM(Protocol):
    """文本 LLM 的协议（接口约定）。"""
    name: str

    def chat(self, prompt: str, *, system: str | None = None,
             temperature: float = 0.0, max_tokens: int | None = None) -> str:
        ...


class OpenAIText:
    """OpenAI 兼容的文本模型客户端（本地 Ollama `/v1` 或任意云端）。

    **`base_url` 必须含版本段**（通常是 `.../v1`）：
        本地：http://localhost:11434/v1
        云端：https://api.deepseek.com/v1
    这里只负责拼 `/chat/completions`。若你误配成 Ollama 根地址，
    会得到 404，报错信息里会提示补 `/v1`。
    """

    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 model: str = "qwen2.5:7b",
                 api_key: str | None = None,
                 timeout: int = 120,
                 cloud_policy: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        # 最近一次调用的 token 用量 (prompt, completion) —— `chat()` 只返回文本，
        # 调用方（如 agent 的预算检查）要读这个 ✅（2026-10-31 补）
        self.last_usage: tuple[int, int] = (0, 0)
        self.timeout = timeout
        self.name = model
        # 出网策略（阶段 1.2）。None = 用默认（原文不许出网、聚合可出网）。
        # 本地地址时这份策略不起作用（本地不叫"出网"）。
        self.cloud_policy = cloud_policy or {}
        # 启动时就把最常见的配置错误挡掉，而不是等跑 3 分钟到 M3 才 404
        if not self.base_url.endswith("/v1") and "/v1" not in self.base_url:
            raise ValueError(
                f"base_url 看起来不像 OpenAI 兼容地址：{self.base_url!r}。"
                "本地 Ollama 请用 http://localhost:11434/v1（末尾要有 /v1）；"
                "云端请用其文档给的 base（通常也含 /v1）。"
            )
        # ⚠️ **API key 必须是纯 ASCII**（2026-10-31 踩到）：
        #    HTTP header 只能用 latin-1 编 ✅
        #    如果 key 里有中文（比如把占位符 `sk-你的key` 直接粘进来 ✅），
        #    会在**发请求时**抛 `UnicodeEncodeError: 'latin-1' codec can't encode...`
        #    —— 那个报错完全不提示"key 有问题" ✅ ✗
        #    → 在这里提前拦住，给一条能直接照着改的提示 ✅
        if self.api_key:
            try:
                self.api_key.encode("latin-1")
            except UnicodeEncodeError:
                bad = [c for c in self.api_key if ord(c) > 127][:5]
                raise ValueError(
                    f"api_key 含有非 ASCII 字符（如 {bad}）—— HTTP header 只能用 latin-1。"
                    "**极可能是把占位符直接粘进来了**（比如 `sk-你的key`）。"
                    "请替换成真实的 key（DeepSeek/OpenAI 的 key 都是纯 ASCII，以 sk- 开头）。"
                ) from None

    # ------------------------------------------------------------------
    def chat(self, prompt: str, *, system: str | None = None,
             temperature: float = 0.0, max_tokens: int | None = None,
             seed: int | None = DEFAULT_SEED,
             kind: str = KIND_TEXT) -> str:
        """发一条对话，返回模型输出的纯文本。

        每次调用记一条 span（`llm.chat`）并写入真实 token 数
        （`usage.prompt_tokens` / `usage.completion_tokens`）。

        :param seed: 随机种子。**temperature=0 时默认塞固定 seed**。
            实测：不加 seed 时同一 prompt 两次回答会不同；加了 `seed=42` 后
            连跑 5 次逐字一致。**seed 才是硬约束，temperature=0 不是。**
        :param kind: 数据类别（阶段 1.2 出网闸门）：
            `KIND_TEXT`（默认，评价原文）/ `KIND_AGGREGATE`（只有数字，如 brief）。
            **默认取最严格的 TEXT** —— 新调用点忘了标，也不会把原文发出去。
        :raises RuntimeError: 连接失败 / HTTP 错误 / 返回为空
        :raises CloudBlocked: 出网策略不允许（hub/core/cloud_guard.py）
        """
        from hub.core.cloud_guard import guard
        from hub.core.tracing import span

        # 出网闸门：本地地址直接放行；云端按 kind + 策略判定（可能脱敏）
        g = guard(self.base_url, kind, prompt, **self.cloud_policy)

        with span("llm.chat", kind="llm", model=self.model,
                  prompt_chars=len(prompt), base_url=self.base_url,
                  data_kind=g.kind, is_cloud=g.is_cloud,
                  redactions=",".join(g.redactions) or None) as sp:
            prompt = g.text          # 用（可能脱敏后的）文本发；本地时原样
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload: dict = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
            }
            # temperature=0 才塞 seed：高温场景下固定 seed 没有意义
            if temperature == 0 and seed is not None:
                payload["seed"] = seed
            if max_tokens:
                payload["max_tokens"] = max_tokens

            raw = self._post(f"{self.base_url}/chat/completions", payload)

            usage = raw.get("usage") or {}
            # **把 token 用量留在实例上**（2026-10-31 补）：
            #   `chat()` 只返回字符串 ✅ → 调用方拿不到 token ✅ ✗
            #   而 agent 的 token 预算（路线图 §7.3）要用它 ✅
            #   → 存一份 `last_usage`，调用方读它（**不改变 chat() 的签名** ✅）
            self.last_usage = (int(usage.get("prompt_tokens") or 0),
                               int(usage.get("completion_tokens") or 0))
            sp.add_tokens(usage.get("prompt_tokens"), usage.get("completion_tokens"))

            choices = raw.get("choices") or []
            content = ((choices[0] if choices else {}).get("message") or {}).get("content", "")
            if not content or not content.strip():
                raise RuntimeError(
                    f"LLM 返回为空（model={self.model}, finish_reason="
                    f"{(choices[0] if choices else {}).get('finish_reason')!r}）"
                )
            sp.add(answer_chars=len(content))
            return content

    # ------------------------------------------------------------------
    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 把服务端的错误体读出来 —— 401/404/429 的原因都在里面，
            # 只报 "HTTP 404" 等于让人猜。
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            hint = ""
            if e.code == 404:
                hint = "（404 常见原因：base_url 少了 /v1，或模型名拼错）"
            elif e.code == 401:
                hint = "（401：api_key 没配或无效）"
            elif e.code == 429:
                hint = "（429：触发了限流，稍后重试或降并发）"
            raise RuntimeError(
                f"LLM 请求失败 HTTP {e.code}{hint}：{url}\n{body}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"连不上 LLM 服务（{url}）：{e}。本地请确认 `ollama serve` 在跑。"
            ) from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 返回的不是 JSON：{e}") from e


def get_text_llm() -> OpenAIText:
    """按 `.env` 配置取文本 LLM（OpenAI 兼容）。

    配置项（`hub/core/config.py::LLMSettings`）：
        HUB_LLM__BASE_URL   含 /v1 的 base（本地默认 http://localhost:11434/v1）
        HUB_LLM__MODEL      qwen2.5:7b 或云端模型名
        HUB_LLM__API_KEY    本地可不填；云端必填
        HUB_LLM__TIMEOUT_S  默认 120

    兜底为什么是 VLM：没配 model 时回落到 `qwen2.5vl:3b`（VLM 的文本模式），
    这样**新克隆只需要拉一个模型**（M2 本来就要用 VLM），门槛最低。
    """
    from hub.core.config import Settings

    s = Settings()
    return OpenAIText(
        base_url=s.llm.base_url or "http://localhost:11434/v1",
        model=s.llm.model or "qwen2.5vl:3b",
        api_key=s.llm.api_key or None,
        timeout=s.llm.timeout_s or 120,
        # 出网策略（阶段 1.2）：本地地址时这份策略不起作用；
        # 指向云端时它决定"原文能不能发" —— 默认 false，只有聚合数字放行。
        cloud_policy=dict(
            allow_text=s.cloud.allow_text,
            allow_aggregate=s.cloud.allow_aggregate,
            do_redact=s.cloud.redact_pii,
        ),
    )
