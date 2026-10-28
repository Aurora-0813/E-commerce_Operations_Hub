# -*- coding: utf-8 -*-
"""
评价主题分类（classify.py）
=========================
把差评分成 7 个主题，**三级降级**保证"每条都有结果"。

    ① 批量 10 条 —— 一次调用，最快（37 批 ≈ 5 分钟）
    ② 逐条重试   —— 只对批量漏掉的那几条重问（救回大部分）
    ③ 关键词兜底 —— LLM 彻底失败时用词表猜（准确率低，标 degraded）

为什么不是"批量失败就把 10 条全重问"：
    批量失败往往只是"某一条把格式带崩了"，
    整批重试等于浪费 9 次成功；只重试漏掉的那条，成本低得多。

校验（防模型跑偏）：
    - id 必须在本次输入集合里（模型会编造 R99）
    - topic 必须是 7 个枚举之一
    - severity/confidence 超范围就**截断**（不报错——3B 不会听话地控制数值）
"""
import json
import re
from dataclasses import dataclass

from hub.analysis.keywords import infer_topic
from hub.llm import TextLLM

# 主题枚举（与 ReviewTopic 枚举、DB CheckConstraint 三处保持一致）
TOPICS = ("quality", "size", "logistics", "service", "desc_mismatch", "misship", "other")

IMPL_LLM = "llm.v1"
IMPL_FALLBACK = "rule_fallback"
MAX_TEXT = 200          # 每条评价喂给模型的最大字数


@dataclass
class ClassifyStats:
    """一次分类运行的统计。"""
    total: int = 0
    batch_ok: int = 0      # 批量一次就成功的条数
    retry_ok: int = 0      # 逐条重试救回的条数
    fallback: int = 0      # 关键词兜底的条数
    batches: int = 0       # 实际发起的批量调用次数

    @property
    def degraded(self) -> int:
        return self.fallback


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    """压空白 + 截断：10 条超长评价会把上下文撑爆。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _build_prompt(items: list[tuple[str, str]]) -> str:
    """拼提示词。items 是 [(R1, 文本), (R2, 文本), ...]。"""
    n = len(items)
    listing = "\n".join(f"[{rid}] {text}" for rid, text in items)
    return (
        f"你是电商评价分类助手。下面有 {n} 条差评，每条以 [R1] 到 [R{n}] 开头。\n\n"
        "请为每条判断它属于哪个问题：\n"
        "- quality        质量：开线/起球/掉色/缩水/线头/变形/面料差\n"
        "- size           尺码：偏大/偏小/版型不符\n"
        "- logistics      物流：慢/丢件/包装破损\n"
        "- service        服务：客服态度/响应慢\n"
        "- desc_mismatch  描述不符：颜色/面料/厚薄和页面不一致\n"
        "- misship        错发漏发\n"
        "- other          抱怨了但说不清具体问题\n\n"
        "如果一条评价同时提到多个问题，选最强烈、最具体的那个。\n"
        "如果评价里有夸有贬，以负面表述为准。\n\n"
        f"输出 JSON 数组，条数必须等于 {n}：\n"
        '[{"id": "R1", "topic": "quality", "severity": 3, "confidence": 0.9}]\n\n'
        "规则：\n"
        f"- id 只能是 R1 到 R{n}\n"
        "- topic 只能是上面 7 个之一\n"
        "- severity 是 1 到 5 的整数\n"
        "- 只输出 JSON，不要解释、不要代码块\n\n"
        f"评价列表：\n{listing}\n"
    )


def _parse_array(text: str) -> list | None:
    """从模型输出里抠 JSON 数组（可能被 markdown 代码块包着）。"""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _clamp(value, lo: float, hi: float, default: float) -> float:
    """把模型给的数值压进 [lo, hi]；解析失败用默认值。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _validate(data: list | None, items: list[tuple[str, str]]) -> dict[str, dict]:
    """校验模型输出，返回 {R1: {topic, severity, confidence}}。

    丢弃规则：
        - id 不在本次输入里（编造）
        - topic 不在枚举里
    """
    valid_ids = {rid for rid, _ in items}
    out: dict[str, dict] = {}
    for row in data or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id", "")).strip()
        if rid not in valid_ids:
            continue
        topic = str(row.get("topic", "")).strip()
        if topic not in TOPICS:
            continue
        out[rid] = {
            "topic": topic,
            "severity": int(_clamp(row.get("severity"), 1, 5, 3)),
            "confidence": round(_clamp(row.get("confidence"), 0.0, 1.0, 0.5), 3),
        }
    return out


def _try_batch(llm: TextLLM, pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """一次分类 N 条，返回 {rid: {...}}；失败返回空 dict。"""
    n = len(pairs)
    try:
        raw = llm.chat(_build_prompt(pairs), max_tokens=120 + 40 * n)
    except Exception as e:                       # noqa: BLE001 上层要降级，不能抛
        print(f"  [分类] 批量调用失败：{e}")
        return {}

    data = _parse_array(raw)
    if data is None:
        print(f"  [分类] 输出不是 JSON 数组：{raw[:80]!r}")
        return {}
    return _validate(data, pairs)


def _to_row(record_id: str, data: dict, impl: str,
            model_version: str | None, degraded: bool) -> dict:
    """组装一行 review_topic 数据。"""
    return {
        "record_id": record_id,
        "topic": data["topic"],
        "severity": data["severity"],
        "confidence": data["confidence"],
        "impl": impl,
        "model_version": model_version,
        "degraded": degraded,
    }


def classify_one(llm: TextLLM, record_id: str, text: str,
                 *, model_version: str | None = None) -> dict:
    """分类单条（调试用）。"""
    pairs = [("R1", _clean(text))]
    parsed = _try_batch(llm, pairs)
    if "R1" in parsed:
        return _to_row(record_id, parsed["R1"], IMPL_LLM, model_version, False)
    topic = infer_topic(text) or "other"
    return _to_row(record_id, {"topic": topic, "severity": 3, "confidence": 0.3},
                   IMPL_FALLBACK, model_version, True)


def classify_many(llm: TextLLM, items: list[dict], *, batch_size: int = 10,
                  model_version: str | None = None) -> tuple[list[dict], ClassifyStats]:
    """批量分类（三级降级）。

    :param items: [{"record_id": "uuid", "text": "..."}]
    :param batch_size: 每批几条（10）
    :param model_version: 写进 review_topic.model_version
    :return: (results, stats)
    """
    stats = ClassifyStats(total=len(items))
    results: list[dict] = []

    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]

        # 给每条分配编号 R1..Rn，建立 编号 → item 的映射
        id_map = {f"R{k + 1}": it for k, it in enumerate(chunk)}
        pairs = [(rid, _clean(it["text"])) for rid, it in id_map.items()]

        stats.batches += 1
        parsed = _try_batch(llm, pairs)          # ① 批量

        # 批量成功的先落
        for rid, it in id_map.items():
            if rid in parsed:
                results.append(_to_row(it["record_id"], parsed[rid], IMPL_LLM,
                                       model_version, False))
                stats.batch_ok += 1

        # 漏掉/格式错的，逐条重试（②）
        for rid, it in id_map.items():
            if rid in parsed:
                continue
            single = _try_batch(llm, [(rid, _clean(it["text"]))])
            if rid in single:
                results.append(_to_row(it["record_id"], single[rid], IMPL_LLM,
                                       model_version, False))
                stats.retry_ok += 1
            else:
                # ③ 关键词兜底
                topic = infer_topic(it["text"]) or "other"
                results.append(_to_row(
                    it["record_id"],
                    {"topic": topic, "severity": 3, "confidence": 0.3},
                    IMPL_FALLBACK, model_version, True,
                ))
                stats.fallback += 1

        print(f"  批次 {stats.batches}: 批量成功 {len(parsed)}/{len(chunk)}")

    return results, stats
