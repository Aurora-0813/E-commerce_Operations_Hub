# -*- coding: utf-8 -*-
"""
DQE 水军分类器训练脚本（train_dqe.py）
=====================================
用 gold_labels.csv 的人工标注，训练一个"文本 → 水军概率"的模型。

整体流程（对应上面的讲解）：
    1. 读标注：gold_labels.csv（记录ID → 是否水军）
    2. 读文本：从 record 表（按 platform_review_id 关联）
    3. 划分：8:2，分层抽样（保证训练/评估的水军比例一致）
    4. 向量化：TF-IDF（字符 2-4）+ TF-IDF（jieba 词）
    5. 训练：逻辑回归（class_weight=balanced）
    6. 评估：精确率 / 召回率 / F1
    7. 保存：模型文件 + 元数据 JSON

为什么读数据库而不是读 xlsx：
    数据库里的文本已经过归一化和 PII 脱敏，
    和线上推理时模型看到的文本完全一致——避免"训练/线上不一致"。

运行（在 backend/ 目录下，先把淘宝评价也导入）：
    python scripts/train_dqe.py
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# 把 backend/ 加入 sys.path，保证能 import hub
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import joblib                                   # noqa: E402
from scipy.sparse import hstack                 # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer   # noqa: E402
from sklearn.linear_model import LogisticRegression           # noqa: E402
from sklearn.metrics import classification_report, f1_score   # noqa: E402
from sklearn.model_selection import train_test_split          # noqa: E402
from sqlalchemy import select                   # noqa: E402

from hub.dqe.ml import jieba_tokenize           # noqa: E402
from hub.persistence.db import get_session      # noqa: E402
from hub.persistence.models import Record as RecordRow   # noqa: E402

GOLD_PATH = _BACKEND_DIR.parent / "data" / "mock" / "gold_labels.csv"
MODEL_DIR = _BACKEND_DIR / "models" / "dqe"


def load_dataset() -> tuple[list[str], list[int]]:
    """读标注 + 关联文本，返回 (文本列表, 标签列表)。

    标签：1 = 水军，0 = 正常
    """
    # 1. 读标注：记录ID → 是否水军
    labels: dict[str, int] = {}
    with GOLD_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            labels[row["记录ID"]] = int(row["是否水军"])

    # 2. 从数据库读文本（按 platform_review_id 关联）
    print(f"标注 {len(labels)} 条，正在数据库里关联文本……")
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_review_id, RecordRow.content_clean)
        ).all()
    text_by_id = {rid: text for rid, text in rows}

    # 3. 只保留"标注里有的、且库里也有的"
    texts: list[str] = []
    ys: list[int] = []
    missing = 0
    for rid, y in labels.items():
        text = text_by_id.get(rid)
        if not text:
            missing += 1
            continue
        texts.append(text)
        ys.append(y)

    print(f"成功关联 {len(texts)} 条，缺失 {missing} 条")
    if missing:
        print("  提示：缺失通常是该平台评价还没导入（先跑 p0 导入淘宝/京东）")
    return texts, ys


def build_features(train_texts: list[str], test_texts: list[str]):
    """构建 TF-IDF 特征（字符级 + 分词级）。"""
    # 字符级：2~4 字滑窗，对错别字/新词鲁棒
    char_vec = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        min_df=2,               # 至少在 2 条评价里出现过（过滤只出现一次的生僻词）
        max_features=50000,     # 最多 5 万个特征（控制维度）
    )
    # 词级：jieba 分词
    # 注意用 jieba_tokenize（模块级函数），不能用 jieba.lcut，
    # 否则 joblib 保存模型时会报 "cannot pickle '_thread.RLock'"。
    word_vec = TfidfVectorizer(
        tokenizer=jieba_tokenize,
        token_pattern=None,     # 用自定义 tokenizer 时必须关掉默认 pattern
        min_df=2,
        max_features=20000,
    )

    # 只在训练集上 fit（避免"偷看"评估集数据，这是数据泄露）
    x_char_train = char_vec.fit_transform(train_texts)
    x_word_train = word_vec.fit_transform(train_texts)
    x_train = hstack([x_char_train, x_word_train]).tocsr()

    x_char_test = char_vec.transform(test_texts)
    x_word_test = word_vec.transform(test_texts)
    x_test = hstack([x_char_test, x_word_test]).tocsr()

    return (char_vec, word_vec), x_train, x_test


def main() -> None:
    # 1. 数据
    texts, ys = load_dataset()
    if len(texts) < 50:
        print("❌ 样本太少（<50），先导入两个平台的评价再训练")
        return

    # 2. 划分（stratify 保证水军比例一致）
    x_train_texts, x_test_texts, y_train, y_test = train_test_split(
        texts, ys, test_size=0.2, random_state=42, stratify=ys,
    )
    print(f"训练集 {len(x_train_texts)} 条，评估集 {len(x_test_texts)} 条")
    print(f"训练集水军比例 {sum(y_train) / len(y_train):.2%}")

    # 3. 特征
    (char_vec, word_vec), x_train, x_test = build_features(x_train_texts, x_test_texts)
    print(f"特征维度：{x_train.shape[1]}")

    # 4. 训练
    clf = LogisticRegression(
        class_weight="balanced",   # 类别不均衡时加大少数类权重
        C=1.0,                     # 正则化强度（越小越保守，越不易过拟合）
        max_iter=1000,
    )
    clf.fit(x_train, y_train)

    # 5. 评估
    y_pred = clf.predict(x_test)
    print("\n=== 评估集指标 ===")
    print(classification_report(y_test, y_pred, target_names=["正常", "水军"], digits=3))
    f1 = f1_score(y_test, y_pred)
    print(f"F1 = {f1:.3f}")

    # 6. 保存模型 + 元数据
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / "spam_lr_v1.joblib"
    meta_path = MODEL_DIR / "spam_lr_v1.json"

    joblib.dump({"char_vec": char_vec, "word_vec": word_vec, "clf": clf}, model_path)

    meta = {
        "version": "v1",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_train": len(x_train_texts),
        "n_test": len(x_test_texts),
        "n_features": int(x_train.shape[1]),
        "metrics": {"f1": round(float(f1), 4)},
        "model_path": str(model_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 模型已保存：{model_path}")
    print(f"✅ 元数据已保存：{meta_path}")


if __name__ == "__main__":
    main()
