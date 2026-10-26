# -*- coding: utf-8 -*-
"""
ML 轨（ml.py）
=============
水军分类器的加载 + 预测，含**降级**（模型缺失/损坏时返回全 0 分数，主链继续）。

为什么需要一个"模块级"的 jieba_tokenize：
    jieba.lcut 是 jieba 内部 Tokenizer 单例的"绑定方法"，
    它持有 threading.RLock。用 joblib 保存 TfidfVectorizer 时，
    如果 tokenizer 直接是 jieba.lcut，pickle 会试图序列化整个 Tokenizer，
    报错：TypeError: cannot pickle '_thread.RLock' object。

    包成模块级函数后，pickle 只记录"模块名 + 函数名"的引用，
    加载时重新 import 这个函数即可，不会碰 jieba 的单例对象。
"""
import json
from pathlib import Path

import jieba
import joblib
from scipy.sparse import hstack

# 模型默认路径：backend/models/dqe/spam_lr_v1.joblib
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "dqe" / "spam_lr_v1.joblib"
)


def jieba_tokenize(text: str) -> list[str]:
    """模块级分词函数（可被 pickle 安全序列化）。

    训练和推理都调这个函数，保证两边分词方式完全一致。
    """
    return jieba.lcut(text)


class SpamModel:
    """水军分类器封装。

    降级语义（对应设计文档 §3.1.4）：
        模型文件不存在 / 加载失败 → degraded=True → predict() 返回全 0
        主链不中断，规则轨照常工作，只是少了 ML 这一路信号。
    """

    def __init__(self, model_path: Path | str | None = None):
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.model: dict | None = None
        self.degraded = True
        self.version = "unknown"
        self._load()

    def _load(self) -> None:
        """加载模型；失败就降级，不抛异常。"""
        if not self.model_path.exists():
            # 没有模型不是错误：rule_only 模式本来就要支持
            return
        try:
            self.model = joblib.load(self.model_path)
            self.degraded = False
            # 读同名元数据 JSON 拿版本号（如 spam_lr_v1.json）
            meta_path = self.model_path.with_suffix(".json")
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                self.version = meta.get("version", "unknown")
        except Exception as e:      # noqa: BLE001  加载失败要兜住，不能拖垮主链
            print(f"[DQE] 模型加载失败，降级为 rule_only：{e}")
            self.model = None
            self.degraded = True

    def predict(self, texts: list[str]) -> list[float]:
        """批量预测水军概率（0~1）。降级时返回全 0。"""
        if self.degraded or self.model is None or not texts:
            return [0.0] * len(texts)

        x_char = self.model["char_vec"].transform(texts)
        x_word = self.model["word_vec"].transform(texts)
        x = hstack([x_char, x_word]).tocsr()
        # predict_proba 返回 [[P(正常), P(水军)]...]，取第 2 列
        return self.model["clf"].predict_proba(x)[:, 1].tolist()
