# -*- coding: utf-8 -*-
"""
归因器（attribute.py）
=====================
对一个「主题 × 平台」格子做归因：让 LLM 看 10 条代表评价，
产出「具体表现 + 逐字引用」。

反幻觉三道：
    1. **预过滤**：quote 不是原文子串的，直接丢掉（记 dropped）
       —— 一条脏证据不该让整条归因作废
    2. **契约校验**：Attribution 的 ground_evidence 再核一遍（双保险）
    3. **confidence 规则算**：不用 LLM 给的（M2 的教训：3B 恒 1.0）

为什么 confidence 用规则：
    证据越多越可信，这是可解释的；模型自评的置信度不可解释也不可信。
"""
import json
import re

from hub.contracts import Attribution
from hub.llm import TextLLM

# 提示词里用中文名，模型更容易理解
TOPIC_CN = {
    "quality": "质量问题",
    "size": "尺码问题",
    "logistics": "物流问题",
    "service": "服务问题",
    "desc_mismatch": "描述不符",
    "misship": "错发漏发",
    "other": "其他问题",
}
PLATFORM_CN = {"taobao": "淘宝", "jd": "京东"}

MAX_QUOTE = 80       # 证据引用最长字数（契约上限）
MAX_EXPLANATION = 300


def _build_prompt(topic: str, platform: str, pairs: list[tuple[str, str]]) -> str:
    """pairs = [(R1, 文本), ...]"""
    n = len(pairs)
    listing = "\n".join(f"[{rid}] {text}" for rid, text in pairs)
    return (
        f"你是电商运营分析助手。下面是「{TOPIC_CN.get(topic, topic)}」类差评"
        f"在「{PLATFORM_CN.get(platform, platform)}」的 {n} 条代表评价。\n\n"
        f"{listing}\n\n"
        "请分析：\n"
        '1. 这些差评里**最主要的 1 到 2 个问题**是什么？'
        '（具体到"缝线""面料""掉色"这样的层次；'
        "不要罗列每一条评价的症状；**不要引用 R 编号**）\n"
        "2. 挑 1 到 5 条最能代表问题的原文，**逐字原样引用**（每条 4 到 80 字）\n\n"
        "输出 JSON：\n"
        '{"explanation": "不超过 300 字，说清具体表现", '
        '"evidence": [{"id": "R1", "quote": "逐字引用"}]}\n\n'
        "铁律：\n"
        "- quote 必须从原文逐字复制，不能改写、不能拼接两条\n"
        "- evidence 最多 5 条\n"
        "- explanation 不超过 300 字\n"
        "- 只输出 JSON，不要解释、不要代码块\n"
    )


def _parse_object(text: str) -> dict | None:
    """从模型输出里抠 JSON 对象（要扛住 3B 的各种不听话）。

    依次尝试：
        1. 去掉 markdown 代码块围栏后直接解析
        2. 用正则抠出最外层 {...} 再解析
    """
    s = text.strip()
    # 1. 去掉 ```json ... ``` 围栏
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()

    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 2. 抠最外层 {...}
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _strip_md(text: str) -> str:
    """去掉模型爱加的 markdown 强调标记（**加粗**、`代码`）。"""
    s = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def _dedupe_sentences(text: str) -> str:
    """去掉重复的句子。

    3B 模型生成长文本时会陷入"复读机"：
        第2句：有些商品的尺码不准导致穿着效果不佳 ，如连衣裙...   ← 有空格
        第3句：有些商品的尺码不准导致穿着效果不佳，如连衣裙...     ← 没空格
        第3句前面还有序号 "3."，第2句是 "2."

    所以比较前要**去掉序号和所有空白**，否则一个空格就骗过去。
    """
    parts = re.split(r"(?<=[。；;！!？?])", text)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        # 把"2. " / "3、" 这类序号去掉，再删掉所有空白，得到用于比较的 key
        key = re.sub(r"^\s*\d+\s*[.、)）]\s*", "", p.strip())
        key = re.sub(r"\s+", "", key)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "".join(out).strip()


def _rule_confidence(n_evidence: int) -> float:
    """证据越多越可信（规则算，不用模型自评）。

    n=1 → 0.6  （低于 0.8，满足契约"高置信需 ≥2 条证据"）
    n=2 → 0.7
    n=3 → 0.8  （有 3 条，满足约束）
    n=4 → 0.9
    n=5 → 0.95
    """
    return round(min(0.95, 0.5 + 0.1 * n_evidence), 3)


def _norm_chars(text: str) -> str:
    """只保留中文/字母/数字（丢掉标点、空格）。"""
    return "".join(ch for ch in text if ch.isalnum())


def _find_in_content(content: str, quote: str) -> str | None:
    """在一条原文里定位 quote，返回**原文中的精确片段**。

    三级匹配（实测 3B 模型的引用问题都出在前两级）：
        ① 精确子串
        ② 去掉尾部标点再试 —— 模型爱把引用补成完整句子（末尾加"。"）
        ③ 规范化（只留中文/字母/数字）后定位，再映射回原文
           —— 容忍中英文标点混用、空格差异

    :return: 原文中的片段；找不到返回 None
    """
    q = (quote or "").strip()
    if not q:
        return None

    # ① 精确
    if q in content:
        return q

    # ② 去尾部标点（。．.,，!！?？;；:： 空格）
    q2 = q.rstrip("。．.,，!！?？;；:： ")
    if len(q2) >= 4 and q2 in content:
        return q2

    # ③ 规范化匹配：去掉标点/空格后找，再映射回原文位置
    nq = _norm_chars(q2)
    if len(nq) < 4:
        return None
    idx = [i for i, ch in enumerate(content) if ch.isalnum()]
    nc = "".join(content[i] for i in idx)
    pos = nc.find(nq)
    if pos < 0:
        return None
    return content[idx[pos]:idx[pos + len(nq) - 1] + 1]


def _locate_quote(quote: str, content_by_id: dict[str, str],
                  claimed_id: str | None) -> tuple[str, str] | None:
    """定位一条 quote 到底来自哪条评价。

    先信模型给的 id；找不到就在**所有原文**里搜——
    模型经常把 R6 的话安到 R5 头上，但它抄的原文是真的。
    以原文为准，不盲信模型给的编号。

    :return: (真实 review_id, 原文中的精确片段)；找不到返回 None
    """
    if claimed_id and claimed_id in content_by_id:
        found = _find_in_content(content_by_id[claimed_id], quote)
        if found:
            return claimed_id, found

    for rid, content in content_by_id.items():
        found = _find_in_content(content, quote)
        if found:
            return rid, found
    return None


def attribute_cell(
    llm: TextLLM,
    topic: str,
    platform: str,
    reviews: list[dict],
    *,
    model_version: str | None = None,
) -> dict | None:
    """对一个格子做归因。

    :param reviews: [{"review_id": "uuid", "text": "..."}]，建议 10 条
    :return: 归因结果 dict；失败返回 None（调用方决定跳过还是兜底）
    """
    if not reviews:
        return None

    id_map = {f"R{k + 1}": r["review_id"] for k, r in enumerate(reviews)}
    content_by_id = {r["review_id"]: r["text"] for r in reviews}
    pairs = [(rid, r["text"][:300]) for rid, r in zip(id_map, reviews)]

    # ---- 调模型 ----
    try:
        raw = llm.chat(_build_prompt(topic, platform, pairs), max_tokens=1000)
    except Exception as e:                        # noqa: BLE001
        print(f"  [归因] {topic}/{platform} 调用失败：{e}")
        return None

    data = _parse_object(raw)
    if data is None:
        # 打印完整输出（截断到 400 字）方便定位：是截断了，还是 JSON 语法坏了
        print(f"  [归因] {topic}/{platform} 输出不是 JSON，原始输出：\n{raw[:400]}")
        return None

    explanation = _strip_md(str(data.get("explanation", "")))[:MAX_EXPLANATION]
    explanation = _dedupe_sentences(explanation)[:MAX_EXPLANATION]
    degraded = False
    if not explanation:
        explanation = "（模型未给出解释）"
        degraded = True

    # ---- 证据定位：三级匹配 + 不信模型给的 ID ----
    evidence: list[dict] = []
    dropped = 0
    seen_ids: set[str] = set()
    for e in (data.get("evidence") or [])[:5]:
        if not isinstance(e, dict):
            continue
        rid = str(e.get("id", "")).strip()
        quote = str(e.get("quote", "")).strip()
        located = _locate_quote(quote, content_by_id, id_map.get(rid))
        if located is None:
            dropped += 1
            continue
        real_id, exact = located
        if real_id in seen_ids:            # 同一句被引用两次，去重
            continue
        seen_ids.add(real_id)
        evidence.append({
            "review_id": real_id,
            "quote": exact[:MAX_QUOTE],    # 存**原文片段**，不是模型给的原话
            "signal": "text",
        })
    if dropped:
        degraded = True

    if not evidence:
        print(f"  [归因] {topic}/{platform} 无可用证据（丢弃 {dropped} 条）")
        return None

    # ---- 契约校验（双保险）----
    try:
        att = Attribution.model_validate(
            {
                "root_cause": topic,
                "confidence": _rule_confidence(len(evidence)),
                "explanation": explanation,
                "evidence": evidence,
            },
            context={"content_by_id": content_by_id},
        )
    except Exception as e:                        # noqa: BLE001
        print(f"  [归因] {topic}/{platform} 契约校验失败：{e}")
        return None

    return {
        # 字段名对齐契约（Attribution.root_cause），不要用 topic
        "root_cause": att.root_cause.value,
        "platform": platform,
        "confidence": att.confidence,
        "explanation": att.explanation,
        "evidence": [ev.model_dump() for ev in att.evidence],
        "dropped": dropped,
        "degraded": degraded,
        "model_version": model_version,
    }
