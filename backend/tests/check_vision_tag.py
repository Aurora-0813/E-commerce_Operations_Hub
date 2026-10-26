# -*- coding: utf-8 -*-
"""
验证 OllamaVision.tag()：跑全部 10 张瑕疵图 + 3 张正常图。

预期：
    瑕疵图 → 破损 / 污渍 / 色差 / 开线 / 其他（不能是 none）
    正常图 → none

运行（在 backend/ 目录下）：
    python tests/check_vision_tag.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.multimodal.vision import OllamaVision   # noqa: E402

REAL_DIR = _BACKEND_DIR.parent / "data" / "mock" / "images_real"

# 文件名里出现这些词 → 算瑕疵图
DEFECT_KEYWORDS = ("破洞", "破损", "污渍", "色差", "开线", "起球",
                   "磨损", "抽丝", "开裂", "脱落", "发黄")


def main() -> None:
    v = OllamaVision()
    imgs = sorted(p for p in REAL_DIR.iterdir()
                  if p.suffix.lower() in {".jpg", ".jpeg", ".png"})

    defects = [p for p in imgs if any(k in p.name for k in DEFECT_KEYWORDS)]
    normals = [p for p in imgs if p not in defects]
    print(f"图片 {len(imgs)} 张｜瑕疵图 {len(defects)} 张｜正常图 {len(normals)} 张\n")

    print("=== 瑕疵图（应该都不是 none）===")
    for p in defects:
        tags = v.tag(str(p))
        print(f"  {p.name:<32} → {tags.defect_type:<6} "
              f"sev={tags.severity} color_dev={tags.color_deviation}")

    print("\n=== 正常图（抽 3 张，应该是 none）===")
    for p in normals[:3]:
        tags = v.tag(str(p))
        print(f"  {p.name:<32} → {tags.defect_type:<6} "
              f"sev={tags.severity} color_dev={tags.color_deviation}")


if __name__ == "__main__":
    main()
