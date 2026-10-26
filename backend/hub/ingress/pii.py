# -*- coding: utf-8 -*-
"""
PII 处理（pii.py）
==================
个人信息（PII）是红线：能丢的丢，必须留的脱敏。

本模块负责：
    1. 判断某列是否是 PII（昵称 → 直接丢）
    2. 对评论文本里的手机号 / 微信号做脱敏
    3. 统计丢了多少（只计数，不留原值）

注意：只做"丢弃/脱敏"，绝不把原始 PII 写日志或落库。
"""
import re

# 手机号：11 位，1 开头
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 微信号：常见形式（wx: xxx / 微信 xxx / 加V xxx）
_WECHAT = re.compile(r"(?:微信|wx|WX|vx|VX|加V|加v)\s*[:：]?\s*[A-Za-z0-9_-]{5,20}", re.IGNORECASE)

# 哪些统一字段属于 PII，必须丢弃
PII_FIELDS = {"nickname"}


def is_pii_field(field: str) -> bool:
    """这个字段是不是 PII（是的话不入库）。"""
    return field in PII_FIELDS


def mask_text(text: str) -> str:
    """对文本里的手机号、微信号做脱敏。

    例： "加微信abc12345 或打 13800138000"
        → "加微信*** 或打 138****8000"
    """
    if not text:
        return text
    # 手机号：保留前 3 后 4
    text = _PHONE.sub(lambda m: f"{m.group(0)[:3]}****{m.group(0)[-4:]}", text)
    # 微信号：整段替换
    text = _WECHAT.sub("微信***", text)
    return text


def has_pii(text: str) -> bool:
    """文本里有没有 PII（用于统计）。"""
    if not text:
        return False
    return bool(_PHONE.search(text) or _WECHAT.search(text))
