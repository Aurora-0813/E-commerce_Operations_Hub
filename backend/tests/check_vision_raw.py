# -*- coding: utf-8 -*-
"""
对比 3 种提示词，找出能让模型"不照抄、真判断"的那种。

用同一张"有破洞"的图测，正确输出应该是 defect_type = 破损。

运行（在 backend/ 目录下）：
    python tests/check_vision_raw.py
"""
import base64
import json
import sys
import urllib.request
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.multimodal.vision import PROMPT as P1   # noqa: E402

REAL_DIR = _BACKEND_DIR.parent / "data" / "mock" / "images_real"
MODEL = "qwen2.5vl:3b"

# P2：CoT + 占位符（把 <> 换掉，并强调别照抄）
P2 = """你是电商商品图质检助手。先仔细观察图片：这是什么商品？什么颜色？有没有破洞、污渍、色差、开线？

观察后，只输出一行 JSON，把尖括号里的内容换成你的判断（不要照抄，不要代码块）：
{"defect_type": "<none或破损或污渍或色差或开线或其他>", "severity": <0到3的整数>, "color_deviation": <0到1的小数>, "ocr_text": <文字或null>, "confidence": <0到1的小数>}"""

# P3：行格式（彻底避开 JSON 照抄）
P3 = """你是电商商品图质检助手。看这张买家秀图片，按下面的格式输出 5 行，不要多余内容：

描述：一句话说明图片里是什么商品、什么颜色
缺陷类型：none 或 破损 或 污渍 或 色差 或 开线 或 错发 或 尺码不符 或 其他
严重度：0 到 3 的整数
色差程度：0 到 1 的小数
置信度：0 到 1 的小数"""

PROMPTS = [("P1 原版（带示例）", P1), ("P2 CoT+占位符", P2), ("P3 行格式", P3)]


def ask(image_path: Path, prompt: str) -> str:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": MODEL,
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
        return json.loads(resp.read().decode("utf-8"))["message"]["content"]


def main() -> None:
    imgs = sorted(p for p in REAL_DIR.iterdir()
                  if p.suffix.lower() in {".jpg", ".jpeg", ".png"})

    # 优先找文件名带"破洞/破损/污渍/色差"的瑕疵图
    defect_kw = ("破洞", "破损", "污渍", "色差", "开线", "起球")
    defects = [p for p in imgs if any(k in p.name for k in defect_kw)]
    normals = [p for p in imgs if p not in defects]

    target = defects[0] if defects else imgs[-1]
    print(f"图片总数 {len(imgs)}｜瑕疵图 {len(defects)}｜测试图：{target.name}\n")

    for label, prompt in PROMPTS:
        print("=" * 60)
        print(f"【{label}】")
        print(ask(target, prompt))

    if normals:
        print("\n" + "=" * 60)
        print(f"【对照：正常图 {normals[0].name}（P3 行格式）】")
        print(ask(normals[0], P3))


if __name__ == "__main__":
    main()
