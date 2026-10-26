# -*- coding: utf-8 -*-
"""
验证 ML 水军分类器：加载、预测、降级。

运行（在 backend/ 目录下）：
    python tests/check_ml.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.dqe.ml import SpamModel          # noqa: E402
from hub.dqe.normalize import normalize   # noqa: E402


def main() -> None:
    # 1. 正常加载
    m = SpamModel()
    print("=== 1. 模型加载 ===")
    print(f"  路径：{m.model_path}")
    print(f"  degraded（降级）= {m.degraded}")

    # 2. 预测：看水军模板 vs 正常评价的分数
    texts = [
        "默认好评",
        "此用户没有填写评价",
        "111111",
        "加微信 abc12345",
        "衣服质量很好，穿着舒服，物流也快",
        "面料不错，就是有点色差",
        "质量不错，值得购买",
    ]
    scores = m.predict([normalize(t) for t in texts])

    print("\n=== 2. 预测分数（水军概率 0~1）===")
    for t, s in zip(texts, scores):
        print(f"  {s:.3f}  {t}")
    print("\n  预期：灌水模板接近 1.0，正常评价接近 0.0")

    # 3. 降级：模型文件不存在时不能崩
    print("\n=== 3. 降级测试（模型不存在）===")
    bad = SpamModel("models/dqe/不存在的模型.joblib")
    print(f"  degraded = {bad.degraded}（应为 True）")
    print(f"  predict  = {bad.predict(['测试文本'])}（应为 [0.0]）")


if __name__ == "__main__":
    main()
