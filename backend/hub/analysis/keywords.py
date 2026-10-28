# -*- coding: utf-8 -*-
"""
关键词兜底（keywords.py）
========================
LLM 彻底失败时，用关键词猜主题——保证"至少有个标签"，而不是空着。

定位：**最后一道防线**，不是主力。
    它只做"粗糙命中"，准确率估计 60~70%，所以打了结果要标 degraded=True。

为什么要有它：
    366 条差评里，如果 20 条 LLM 失败，全标 other 会污染统计；
    关键词能救回一大半（差评里问题词通常很直接）。

顺序很重要：从"具体"到"笼统"。
    比如"包装破了，衣服也破了"——先命中 logistics（包装破），
    而不是 quality（破），因为物流破损是更具体的归因。
"""
from typing import Optional

# key = 主题，value = 关键词元组
# 顺序即优先级：misship → desc_mismatch → size → logistics → service → quality
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "misship": (
        "发错", "错发", "漏发", "少发", "漏了", "发成", "不是我要的", "发错货",
    ),
    "desc_mismatch": (
        "色差", "和图片不", "描述不符", "货不对板", "与描述", "实物和", "跟图片",
    ),
    "size": (
        "偏大", "偏小", "尺码", "大一码", "小一码", "版型", "肩宽", "腰围",
        "裤长", "不合身", "穿不上", "太大", "太小",
    ),
    "logistics": (
        "物流", "快递", "发货", "配送", "包装破", "压破", "太慢", "很慢",
        "等了一周", "还没到", "没收到",
    ),
    "service": (
        "客服", "态度", "售后", "不理", "敷衍", "没人管", "推诿",
    ),
    "quality": (
        "开线", "起球", "掉色", "缩水", "线头", "破", "质量", "面料",
        "扎人", "变形", "纽扣", "透", "薄",
    ),
}


def infer_topic(text: Optional[str]) -> Optional[str]:
    """从评价文字猜主题。

    :return: 命中的主题；没命中返回 None（调用方会兜底成 "other"）
    """
    if not text:
        return None
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(k in text for k in keywords):
            return topic
    return None
