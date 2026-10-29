# -*- coding: utf-8 -*-
"""
验证模型能不能写"合规且具体"的差评回复。

为什么要测：
    `review.draft_reply`（L1）的内容如果让 LLM 写，
    最大的风险是**乱承诺**——"给您退款""明天一定到""双倍赔偿"。
    客服没这个权限，承诺了做不到就是二次投诉。

    所以先测 10 条，看模型会不会踩雷，再决定用 LLM 还是退回模板。

**提示词与策略的演进（实测记录，别走回头路）**：

    版本              模型            红线  具体引用  雷同/贴切
    ------------------------------------------------------------------
    v1 约束版         qwen2.5vl:3b    2 处   6/10     —
    v2 严约束(7条)    qwen2.5vl:3b    0 处   2/10     38 字，被压空
    v2 严约束(7条)    qwen2.5:3b      0 处   0/10     12 字，"已记录"
    v3 宽松           qwen2.5:7b      2 处   9/10     好
    v4 宽松+固定话术  qwen2.5:7b      0 处   9/10     ❌ 全写"联系客服办理退换"
    v5 代码给处理方向 qwen2.5:7b      0 处   8/10     ✅ 每条不同且贴切

**三条教训**：
    1. **一上来加一堆"不许"，会把小模型压成"只说最安全的话"**（v2）。
    2. **在提示词里塞固定话术 = 用合规指标换掉了多样性**（v4 十句一模一样，
       而且对"物流慢/客服差"答非所问）。指标骗过了我，眼睛没骗过。
    3. **"该做什么"由代码定（物流问题找物流方），"怎么说"交给 LLM**（v5）。
       这也符合决策 #14「参数由代码算，LLM 只选策略」。

合规规则（差评回复的硬禁忌）：
    承诺赔偿 / 承诺时间 / 绝对化 / 索要隐私 / 甩锅

运行（在 backend/ 目录下）：
    python tests/check_reply_gen.py
"""
import re
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.llm import get_text_llm   # noqa: E402

# 每条差评配一个"处理方向"。**这段由代码给，不由 LLM 编**：
#   · 业务判断（物流问题找物流、客服问题找主管）是代码的知识
#   · LLM 只负责"怎么说得像人"
# 这也符合决策 #14「参数由代码算，LLM 只选策略」——处理方向就是策略。
#
# 反面教材：如果把"处理方式"也写死在提示词里（"只写可以联系客服办理退换"），
# 10 条回复会全是同一句，而且对物流/客服问题答非所问。
CASES = [
    ("衣服开线了，穿两次就破了，太失望", "会反馈给工厂改进缝线工艺"),
    ("面料扎人，穿着很不舒服，皮肤都红了", "会反馈给面料供应商核实"),
    ("尺码偏小，XL 穿起来像 L，建议买大一码", "会核对尺码表，把偏差标注出来"),
    ("物流太慢，下单十天还没到，催了三次", "会联系物流方加急跟进"),
    ("发错货了，我要黑色发来白色", "会核对发货记录，避免再出错"),
    ("色差太大，图片是红色实物是玫红", "会调整详情页的图片和描述"),
    ("客服态度极差，问了几次都不回", "会反馈给客服主管核实"),
    ("说好的退运费也没退，骗子", "会核对退款记录"),
    ("洗了一次就缩水，小了一圈没法穿了", "会反馈给工厂核实缩水问题"),
    ("纽扣掉了两颗，质量堪忧", "会反馈给工厂加强辅料检查"),
]

# 合规规则：(等级, 名字, 正则, 说明)
#
# 为什么分三档（实测教训）：
#   第一版把"换货/退款"也算违规，结果 7B 有 4 条被标记——但**店铺本来就有退换货的权限**，
#   真实客服话术就是"可以为您办理退换"。禁掉它反而不像人话。
#   真正的高风险是"承诺金额"和"索要隐私"，那个必须改；
#   "承诺时间/绝对化/甩锅"是建议改；"承诺退换货"交给人判断。
RULES = [
    # 🔴 红线：必须改（金额、隐私）
    ("红线", "承诺金额/赔偿",
     r"(赔偿|赔付|补偿|免单|返现|双倍|三倍|赔您|赔你|退您.{0,4}元|赔.{0,4}元)",
     "金额是硬承诺，做不到就是二次投诉"),
    ("红线", "索要隐私",
     r"(手机号|身份证|微信号?|银行卡|发个地址给我)",
     "合规红线"),
    # 🟡 黄线：建议改（时间、绝对化、甩锅）
    ("黄线", "承诺时间",
     r"(\d+\s*(小时|天|工作日)内|明天|今天)[^，。！？]{0,6}(到|解决|处理完|发出|补发|送到)",
     "时效不可控"),
    ("黄线", "绝对化",
     r"(绝对|百分百|100%|保证不|一定不会|肯定不)", "广告法风险"),
    ("黄线", "甩锅",
     r"(与我们无关|是快递的问题|是厂家的问题|不关我们的事)", "激化矛盾"),
    # ⚪ 提示：人工判断（店铺本分内的退换货，不算错，但要确认政策）
    ("提示", "承诺具体处理",
     r"(为您|帮您|给您|给您们|马上|尽快)[^，。！？]{0,8}(退款|退货|换货|补发|安排换)",
     "店铺一般有这个权限，但要看是否符合售后政策"),
]

# v5：宽松 + **一条**引导 + **代码给处理方向**。
# 处理方向（remedy）是业务判断，由代码算；LLM 只负责"怎么说得像人"。
PROMPT = """你是电商店铺的店主。买家给了一条差评，请写一条回复。

买家评价：「{review}」

我们会怎么处理：{remedy}

怎么写：
- 40~80 个字，像人说话
- 先回应买家遇到的具体问题（用他自己的说法），再说上面这个处理方式
- **不要承诺退款、换货、补发、赔偿，也不要承诺时间**
- 话要实在，别空喊"会改进"

回复："""


def check_compliance(text: str) -> list[tuple[str, str, str]]:
    """返回触发的规则 [(等级, 名字, 命中片段)]。"""
    hits = []
    for tier, name, pattern, _ in RULES:
        m = re.search(pattern, text)
        if m:
            hits.append((tier, name, m.group(0)))
    return hits


# 空话检测：这些词出现且没有具体问题词 = 敷衍
VAGUE_WORDS = ("这个问题", "您的问题", "该问题", "尽快处理", "请耐心等待")
# 套话开头（3B 最爱的那个）
BOILERPLATE_START = "非常抱歉给您带来"


def quality_check(reply: str, review: str) -> list[str]:
    """质量检查（不算违规，但说明回复很敷衍）。"""
    issues = []
    if reply.startswith(BOILERPLATE_START):
        issues.append("套话开头")
    if any(w in reply for w in VAGUE_WORDS) and not _has_specific_word(reply, review):
        issues.append("只说'问题'不引用原话")
    return issues


def _has_specific_word(reply: str, review: str) -> bool:
    """回复里有没有买家原话里的具体问题词（长度≥2 的名词片段）。"""
    candidates = ["开线", "扎人", "尺码", "物流", "发错", "色差", "客服",
                  "运费", "缩水", "纽扣", "退货", "退款", "款式", "颜色"]
    return any(c in review and c in reply for c in candidates)


def main() -> None:
    llm = get_text_llm()
    print(f"模型：{llm.name}｜测试 {len(CASES)} 条差评回复\n")

    red = yellow = info = 0
    too_short = 0
    t0 = time.time()
    results = []

    for i, (review, remedy) in enumerate(CASES, 1):
        try:
            reply = llm.chat(PROMPT.format(review=review, remedy=remedy),
                             max_tokens=200).strip()
        except Exception as e:                       # noqa: BLE001
            print(f"[{i}] 生成失败：{e}")
            continue

        reply = re.sub(r"^回复[:：]\s*", "", reply).strip()
        flags = check_compliance(reply)
        for tier, _, _ in flags:
            if tier == "红线":
                red += 1
            elif tier == "黄线":
                yellow += 1
            else:
                info += 1
        short = len(reply) < 30
        if short:
            too_short += 1
        results.append((review, reply, flags, short))

        has_red = any(t == "红线" for t, _, _ in flags)
        mark = "❌" if has_red else ("⚠️" if (flags or short) else "✅")
        q_issues = quality_check(reply, review)
        print(f"[{i}] {mark} 差评：{review}")
        print(f"     处理方向：{remedy}")
        print(f"     回复：{reply}")
        line = f"     字数 {len(reply)}"
        if flags:
            line += "｜" + "、".join(f"{t}:{n}({s})" for t, n, s in flags)
        if q_issues:
            line += f"｜质量：{'、'.join(q_issues)}"
        if not _has_specific_word(reply, review):
            line += "｜❌没引用原话里的问题词"
        elif not has_red:
            line += "｜✅"
        print(line)
        print()

    elapsed = time.time() - t0
    n = len(results)
    print("=" * 70)
    print(f"共 {n} 条｜耗时 {elapsed:.1f}s（平均 {elapsed / max(n, 1):.1f}s/条）")
    print(f"🔴 红线 {red} 处｜🟡 黄线 {yellow} 处｜⚪ 提示 {info} 处"
          f"｜过短（<30 字）{too_short} 条")

    # 质量统计
    boiler = sum(1 for _, r, _, _ in results if r.startswith(BOILERPLATE_START))
    vague = sum(1 for rev, r, _, _ in results
                if any(w in r for w in VAGUE_WORDS) and not _has_specific_word(r, rev))
    specific = sum(1 for rev, r, _, _ in results if _has_specific_word(r, rev))
    print(f"套话开头：{boiler}/{n}｜只说'问题'不引用原话：{vague}/{n}"
          f"｜引用了具体问题词：{specific}/{n}")

    # 多样性：回复之间不能长得一样（同一句话反复出现 = 模板化敷衍）
    dup_phrases = ["可以联系客服办理退换", "已记录", "会有专人跟进", "尽快为您解决"]
    for ph in dup_phrases:
        cnt = sum(1 for _, r, _, _ in results if ph in r)
        if cnt >= 3:
            print(f"⚠️ 有 {cnt}/{n} 条都含「{ph}」——太雷同了")

    print("\n=== 结论 ===")
    # 三档判断：
    #   红线（金额/隐私）必须 0 —— 这两类是硬伤，人工改了也可能漏
    #   具体性必须够 —— 否则"已记录，会有专人跟进"这种空话也算过关
    #   黄线/提示可以有几条 —— L1 只是草稿，flags 会标给人工看
    if red == 0 and specific >= n * 0.7 and too_short == 0:
        print("  ✅ 无红线 + 具体 → 用 LLM 生成（黄线/提示由人工看 flags）")
    elif red <= 1 and specific >= n * 0.5:
        print("  ⚠️ 红线 ≤1 → 可用，但红线必须人工复核后再发")
    else:
        print("  ❌ 红线过多或不够具体 → 提高提示词约束，或退回模板")
        print("     注意：**不要一上来加一堆'不许'**——实测 3B 会被压成")
        print("           '已记录，会安排专人跟进'（12 个字，等于没回复）")


if __name__ == "__main__":
    main()
