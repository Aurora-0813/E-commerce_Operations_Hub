# -*- coding: utf-8 -*-
"""
字段归一化（normalize.py）
=========================
把平台导出的"脏值"变成干净的结构化值：

    金额   "￥1,299.00"  → Decimal("1299.00")
    评分   "好评"/"5星"  → 5
    时间   "2026-10-01 15:00:00" → datetime
    整数   "1,200"       → 1200

为什么金额用 Decimal：
    float 会丢精度（199.00 可能变 198.9999），钱必须用 Decimal。
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# 金额里要剔除的字符：货币符号、千分位逗号、空格
_MONEY_CLEAN = re.compile(r"[￥¥$,，\s元]")

# 淘宝的"好/中/差"评 → 星级
_TB_RATING = {"好评": 5, "中评": 3, "差评": 1}


def parse_money(value) -> Decimal | None:
    """把 "￥1,299.00" 变成 Decimal("1299.00")；解析不了返回 None。"""
    if value is None:
        return None
    s = _MONEY_CLEAN.sub("", str(value))
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_rating(value, platform: str) -> int | None:
    """把评分归一成 1~5 的整数。

    淘宝：好/中/差 → 5/3/1
    京东：1~5 星（可能是数字、也可能是 "5星" / "五星"）
    """
    if value is None:
        return None
    s = str(value).strip()

    # 淘宝的好中差
    if s in _TB_RATING:
        return _TB_RATING[s]

    # 数字（含 "5" / "5星" / "5.0"）
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    n = int(float(m.group(1)))
    # 星级范围保护：1~5，超范围视为无效
    return n if 1 <= n <= 5 else None


def parse_datetime(value) -> datetime | None:
    """把时间字符串变成 datetime；解析不了返回 None。

    兼容几种常见格式：带秒 / 不带秒 / 斜杠分隔 / 只有日期。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_date(value) -> date | None:
    """把日期字符串变成 date（销售数据用）。"""
    dt = parse_datetime(value)
    return dt.date() if dt else None


def parse_int(value) -> int | None:
    """把 "1,200" 变成 1200；解析不了返回 None。"""
    if value is None:
        return None
    s = re.sub(r"[,，\s]", "", str(value))
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None
