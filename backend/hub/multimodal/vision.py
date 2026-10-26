# -*- coding: utf-8 -*-
"""
Vision 打标（vision.py）
=======================
把图片"读成"结构化标签（VisionTags）。

三种实现，接口一致（VisionModel 协议）：
    MockVision    —— 按颜色模拟（离线、毫秒级，用于纯管道测试）
    OllamaVision  —— 本地 Qwen2.5-VL（Ollama 的 /api/chat，图片不出本机）
    RealVision    —— 云端 Vision（占位，需要 API key）

为什么用 Ollama 原生接口而不是 OpenAI SDK：
    /api/chat 更直接，且不依赖 openai 包；用标准库 urllib 发请求，零额外依赖。
"""
import base64
import json
import re
import urllib.request
from pathlib import Path
from typing import Protocol

from PIL import Image

from hub.contracts import VisionTags

# 提示词：要求模型只返回 JSON，字段和 VisionTags 对齐。
#
# 三个踩过的坑：
#   1. 给"带具体值"的 JSON 示例，小模型会原样照抄（实测有破洞也输出 none）。
#      → 改成"先描述、再判断"，示例用占位符 <...>。
#   2. 选项里放"其他"，模型不确定时就选"其他"来回避判断（正常图被判成其他）。
#      → 明确"没问题必须填 none""有具体问题优先选具体类别"。
#   3. 只给类别名不给定义，模型不知道"色差"指什么（单张图看不到"该是什么颜色"）。
#      → 每个类别给可判断的定义（色差 = 图里出现不自然的颜色块）。
PROMPT = """你是电商商品图质检助手。先仔细观察图片：这是什么商品？什么颜色？有没有质量问题？

观察后，只输出一行 JSON，把尖括号里的内容换成你的判断（不要照抄，不要代码块）：
{"defect_type": "<类别>", "severity": <0到3的整数>, "color_deviation": <0到1的小数>, "ocr_text": <文字或null>, "confidence": <0到1的小数>}

defect_type 只能从下面选一个：
- none：没有任何质量问题
- 破损：破洞、撕裂、磨损、印花脱落
- 污渍：脏污、发黄、明显脏点
- 色差：图片里出现明显不自然的颜色块（比如黑色衣服上有一块红色）
- 开线：缝线崩开、抽丝、线头
- 其他：起球、掉毛等上面没列出的问题

注意：
- 确实没有质量问题，必须填 none，不要为了保险选"其他"。
- 有明确问题时，优先选最具体的类别，不要笼统选"其他"。"""

_VALID_DEFECTS = {"none", "破损", "污渍", "色差", "开线", "错发", "尺码不符", "其他"}


class VisionModel(Protocol):
    """Vision 模型的协议（接口约定）。"""
    name: str

    def tag(self, path: str) -> VisionTags:
        ...


# ==================== Mock：按颜色模拟 ====================

class MockVision:
    """Mock Vision：按图片颜色模拟（离线、毫秒级）。

    规则：
        暗像素占比 > 30%       → 破损
        偏品红（R 高 G 低 B 高） → 色差
        其余                   → 正常
    """

    name = "mock-vision-v1"

    def tag(self, path: str) -> VisionTags:
        img = Image.open(path).convert("RGB").resize((32, 32))
        pixels = list(img.getdata())
        n = len(pixels)

        dark_ratio = sum(1 for r, g, b in pixels if (r + g + b) / 3 < 80) / n
        avg_r = sum(p[0] for p in pixels) / n
        avg_g = sum(p[1] for p in pixels) / n
        avg_b = sum(p[2] for p in pixels) / n

        if dark_ratio > 0.30:
            return VisionTags(defect_type="破损", severity=3,
                              color_deviation=0.0, confidence=0.92)
        if avg_r > 180 and avg_g < 100 and avg_b > 100:
            return VisionTags(defect_type="色差", severity=2,
                              color_deviation=0.8, confidence=0.88)
        return VisionTags(defect_type="none", severity=0,
                          color_deviation=0.0, confidence=0.95)


# ==================== 本地：Ollama + Qwen2.5-VL ====================

class OllamaVision:
    """本地 Qwen2.5-VL（通过 Ollama 的 /api/chat）。

    优点：图片不出本机（零外传）、免费。
    缺点：慢（3B 模型每张约 5~15 秒），大批量要耐心。
    """

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5vl:3b", timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = model

    def tag(self, path: str) -> VisionTags:
        # 1. 读图 → base64（Ollama 的 images 字段直接吃 base64，不要 data: 前缀）
        b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")

        # 2. 调 /api/chat（非流式、温度 0，保证输出稳定）
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": PROMPT,
                "images": [b64],
            }],
            "stream": False,
            "options": {"temperature": 0},
        }
        raw = self._post(f"{self.base_url}/api/chat", payload)
        text = raw.get("message", {}).get("content", "")

        # 3. 解析 JSON → VisionTags
        return self._parse(text)

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse(self, text: str) -> VisionTags:
        """从模型输出里抠 JSON，并做兜底校验（模型可能不听话）。"""
        m = re.search(r"\{.*\}", text, re.S)     # 可能套了 markdown 代码块
        if not m:
            raise ValueError(f"模型没返回 JSON：{text[:120]!r}")

        data = json.loads(m.group(0))

        defect = str(data.get("defect_type", "其他"))
        if defect not in _VALID_DEFECTS:
            defect = "其他"

        severity = int(data.get("severity", 0) or 0)
        severity = min(3, max(0, severity))

        color_dev = float(data.get("color_deviation", 0.0) or 0.0)
        color_dev = min(1.0, max(0.0, color_dev))

        confidence = float(data.get("confidence", 0.5) or 0.5)
        confidence = min(1.0, max(0.0, confidence))

        ocr = data.get("ocr_text")
        if ocr is not None:
            ocr = str(ocr).strip() or None

        return VisionTags(
            defect_type=defect,    # type: ignore[arg-type]
            severity=severity,
            color_deviation=color_dev,
            ocr_text=ocr,
            confidence=confidence,
        )


# ==================== 云端：占位 ====================

class RealVision:
    """云端 Vision（占位）：通义/智谱/OpenAI 等，需要 API key。"""

    name = "real-vision"

    def tag(self, path: str) -> VisionTags:
        raise NotImplementedError("云端 Vision 尚未接入，请先用 Ollama 或 Mock")


def get_vision(model: str = "mock") -> VisionModel:
    """按名字取 Vision 实现。"""
    if model == "mock":
        return MockVision()
    if model in ("ollama", "qwen", "qwen2.5vl", "local"):
        return OllamaVision()
    return RealVision()
