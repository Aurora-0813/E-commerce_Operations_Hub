# -*- coding: utf-8 -*-
"""
用 gold_labels.csv 评估 DQE 的准确率（误杀率 / 召回率）。

口径：
    真值：gold_labels 的"是否水军"（1=水军，0=正常）
    预测：dqe_decision 为 drop 或 review，视为"判为可疑"

运行（在 backend/ 目录下）：
    python tests/check_accuracy.py
"""
import csv
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select          # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402

GOLD_PATH = _BACKEND_DIR.parent / "data" / "mock" / "gold_labels.csv"


def main() -> None:
    # 1. 读标注
    labels: dict[str, int] = {}
    with GOLD_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            labels[row["记录ID"]] = int(row["是否水军"])

    # 2. 读预测
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_review_id, RecordRow.dqe_decision)
        ).all()

    # 3. 混淆矩阵
    tp = fp = tn = fn = 0
    for rid, decision in rows:
        if rid not in labels:
            continue
        truth_spam = labels[rid] == 1
        pred_spam = decision in ("drop", "review")
        if truth_spam and pred_spam:
            tp += 1
        elif truth_spam and not pred_spam:
            fn += 1
        elif not truth_spam and pred_spam:
            fp += 1
        else:
            tn += 1

    total = tp + fp + tn + fn
    print(f"评估样本：{total} 条（gold_labels 里能对上库的）\n")
    print("混淆矩阵：")
    print(f"  真水军 且 判可疑（TP）：{tp}")
    print(f"  真水军 但 判正常（FN，漏判）：{fn}")
    print(f"  真正常 但 判可疑（FP，误杀）：{fp}")
    print(f"  真正常 且 判正常（TN）：{tn}")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0

    print(f"\n精确率（判可疑里真是水军的比例）：{precision:.3f}")
    print(f"召回率（真水军里被抓到的比例）  ：{recall:.3f}")
    print(f"误杀率（正常评价被误判的比例）  ：{fpr:.3f}  ← 越低越好")


if __name__ == "__main__":
    main()
