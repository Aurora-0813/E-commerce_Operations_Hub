# -*- coding: utf-8 -*-
"""
文字补类别（text_hint.py）
=========================
跨模态互补：**图片负责"发现有没有问题"，文字负责"定性是什么问题"**。

为什么需要它：
    Vision 模型（尤其 3B）经常"看到了有问题、但分不清是污渍还是破损"，
    输出成"其他"。而评价文字里，顾客往往会直接说"掉色""开线""破洞"。

策略（保守）：
    只在图片判成"其他"时才用文字补；
    图片已经给出了具体类别（破损/污渍/色差/开线）时，尊重图片的判断。

关键词顺序：从"具体"到"笼统"——
    色差 → 开线 → 污渍 → 破损
    因为"破损"的关键词（破、烂）太宽泛，放最后，避免抢走更精确的类别。
"""
from typing import Optional

TEXT_DEFECT_MAP: dict[str, tuple[str, ...]] = {
    "色差": ("色差", "掉色", "褪色", "变色", "颜色不对", "和图片不一样", "发白"),
    "开线": ("开线", "脱线", "线头", "抽丝", "崩线"),
    "污渍": ("污渍", "脏", "油渍", "污点", "发黄", "黄了"),
    "破损": ("破洞", "破损", "破了", "烂了", "裂开", "开裂", "磨破"),
}


def infer_defect_from_text(text: Optional[str]) -> Optional[str]:
    """从评价文字里推断缺陷类别。

    :return: 命中的类别（色差/开线/污渍/破损）；没命中返回 None。
    """
    if not text:
        return None
    for category, keywords in TEXT_DEFECT_MAP.items():
        if any(k in text for k in keywords):
            return category
    return None
