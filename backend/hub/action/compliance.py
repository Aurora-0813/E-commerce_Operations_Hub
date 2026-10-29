# -*- coding: utf-8 -*-
"""
回复合规校验（compliance.py）
===========================
对外内容（差评回复）的硬禁忌，分三档：

    🔴 红线：承诺金额/赔偿、索要隐私   → 必须改，人工复核后才能发
    🟡 黄线：承诺时间、绝对化、甩锅     → 建议改
    ⚪ 提示：承诺退换货               → 店铺本分内，人工判断

**为什么不全算违规**（实测教训）：
    第一版把"换货/退款"也算红线，结果 7B 有 4 条被标——
    但**店铺本来就有退换货的权限**，真实客服话术就是"可以为您办理退换"。
    真正的高风险是"承诺金额"和"索要隐私"。

这个模块同时被 `review.draft_reply`（写草稿时打 flags）和
`tests/check_reply_gen.py`（提示词实验）使用。
"""
import re

# (等级, 名字, 正则, 说明)
RULES: list[tuple[str, str, str, str]] = [
    # 🔴 红线
    ("红线", "承诺金额/赔偿",
     r"(赔偿|赔付|补偿|免单|返现|双倍|三倍|赔您|赔你|退您.{0,4}元|赔.{0,4}元)",
     "金额是硬承诺，做不到就是二次投诉"),
    ("红线", "索要隐私",
     r"(手机号|身份证|微信号?|银行卡|发个地址给我)",
     "合规红线"),
    # 🟡 黄线
    ("黄线", "承诺时间",
     r"(\d+\s*(小时|天|工作日)内|明天|今天)[^，。！？]{0,6}(到|解决|处理完|发出|补发|送到)",
     "时效不可控"),
    ("黄线", "绝对化",
     r"(绝对|百分百|100%|保证不|一定不会|肯定不)", "广告法风险"),
    ("黄线", "甩锅",
     r"(与我们无关|是快递的问题|是厂家的问题|不关我们的事)", "激化矛盾"),
    # ⚪ 提示
    ("提示", "承诺具体处理",
     r"(为您|帮您|给您|给您们|马上|尽快)[^，。！？]{0,8}(退款|退货|换货|补发|安排换)",
     "店铺一般有这个权限，但要看是否符合售后政策"),
]


def check(text: str) -> list[tuple[str, str, str]]:
    """扫描一段文本，返回 [(等级, 名字, 命中片段)]。命中为空表示合规。"""
    hits = []
    for tier, name, pattern, _ in RULES:
        m = re.search(pattern, text or "")
        if m:
            hits.append((tier, name, m.group(0)))
    return hits


def red_count(hits: list[tuple[str, str, str]]) -> int:
    return sum(1 for t, _, _ in hits if t == "红线")


def flag_strings(hits: list[tuple[str, str, str]]) -> list[str]:
    """转成能存进 `review_reply_draft.compliance_flags` 的字符串列表。"""
    return [f"{tier}:{name}:{snippet}" for tier, name, snippet in hits]
