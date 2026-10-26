# -*- coding: utf-8 -*-
"""
对比"单阶段" vs "两阶段"，看两阶段到底值不值。

对每张瑕疵图跑：
    单阶段：现在用的 PROMPT（一次调用出类别）
    两阶段：先问"有没有问题"（二分类），再问"属于哪类"（细分类）

运行（在 backend/ 目录下）：
    python tests/check_two_stage.py
"""
import base64
import json
import sys
import urllib.request
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.multimodal.vision import OllamaVision   # noqa: E402

REAL_DIR = _BACKEND_DIR.parent / "data" / "mock" / "images_real"
DEFECT_KEYWORDS = ("破洞", "破损", "污渍", "色差", "开线", "起球",
                   "磨损", "抽丝", "开裂", "脱落", "发黄")

# 第 1 问：二分类（最简单的问题）
S1 = "这张图里的衣服有没有质量问题？只回答两个字：有 或 没有。"
# 第 2 问：给定"有问题"后，只做类别选择
S2 = "这张图里的衣服有什么质量问题？只回答一个类别：破损、污渍、色差、开线、其他。"


def ask(image_path: Path, prompt: str) -> str:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": "qwen2.5vl:3b",
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))["message"]["content"].strip()


def main() -> None:
    v = OllamaVision()
    imgs = sorted(p for p in REAL_DIR.iterdir()
                  if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    defects = [p for p in imgs if any(k in p.name for k in DEFECT_KEYWORDS)]
    normals = [p for p in imgs if p not in defects]

    print(f"{'图片':<30} {'单阶段':<6} {'二分类':<4} {'细分类':<6}")
    print("-" * 60)

    for p in defects:
        single = v.tag(str(p)).defect_type
        s1 = ask(p, S1)
        s2 = ask(p, S2) if "有" in s1 else "-"
        print(f"{p.name:<30} {single:<6} {s1:<4} {s2:<6}")

    print("\n--- 正常图（应该：单阶段 none / 二分类 没有）---")
    for p in normals[:3]:
        single = v.tag(str(p)).defect_type
        s1 = ask(p, S1)
        print(f"{p.name:<30} {single:<6} {s1:<4}")


if __name__ == "__main__":
    main()
