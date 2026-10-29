# -*- coding: utf-8 -*-
"""
评价主题分类（classify.py）
=========================
把差评分成 7 个主题，**支持一条评价多个问题**（多标签），三级降级保证"每条都有结果"。

    ① 批量 10 条 —— 一次调用，最快
    ② 逐条重试   —— 只对批量漏掉的那几条重问
    ③ 关键词兜底 —— LLM 彻底失败时用词表猜（准确率低，标 degraded）

**输出格式为什么是"行文本"而不是 JSON 数组**（实测对比）：
    P1 JSON 数组（topics 是列表）  0/10  ← 3B 陷入复读："misship,service,misship,service..."
    P2 行格式（最多 2 个）         8/10  ← ✅
    P4 行格式 + 严重度             7/10  ← ✅ 本实现
    小模型对"嵌套 JSON 数组"会跑飞，对"每条一行"很稳。

**为什么最多 2 个问题**：
    真实的差评大多 1~2 个问题；放开到 3 个以上，3B 会开始胡说。
    超过 2 个的，取前两个（按严重度排序）。
"""
import hashlib
import re
from dataclasses import dataclass

from hub.analysis.keywords import infer_topic
from hub.llm import TextLLM

# 主题枚举（与 ReviewTopic 枚举、DB CheckConstraint 三处保持一致）
TOPICS = ("quality", "size", "logistics", "service", "desc_mismatch", "misship", "other")

IMPL_LLM = "llm.v1"
IMPL_FALLBACK = "rule_fallback"
MAX_TEXT = 200          # 每条评价喂给模型的最大字数
MAX_TOPICS = 2          # 一条评价最多识别几个问题


@dataclass
class ClassifyStats:
    """一次分类运行的统计。"""
    total: int = 0
    batch_ok: int = 0      # 批量一次就成功的条数
    retry_ok: int = 0      # 逐条重试救回的条数
    fallback: int = 0      # 关键词兜底的条数
    batches: int = 0       # 实际发起的批量调用次数
    multi_label: int = 0   # 识别出 2 个问题的条数

    @property
    def degraded(self) -> int:
        return self.fallback


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    """压空白 + 截断：10 条超长评价会把上下文撑爆。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


PROMPT_TEMPLATE = (
    "你是电商评价分类助手。下面有 {n} 条差评。\n\n"
    "请判断每条提到了哪些问题（**最多 {max_topics} 个**，只从下面选），以及严重度：\n"
    "- quality        质量：开线/起球/掉色/缩水/线头/变形/面料差\n"
    "- size           尺码：偏大/偏小/版型不符\n"
    "- logistics      物流：慢/丢件/包装破损\n"
    "- service        服务：客服态度/响应慢/退款纠纷\n"
    "- desc_mismatch  描述不符：颜色/面料/厚薄和页面不一致\n"
    "- misship        错发漏发\n"
    "- other          抱怨了但说不清具体问题\n\n"
    "按这个格式输出，每条一行，不要其他内容：\n"
    "R1: quality,size | 3\n"
    "R2: logistics | 2\n\n"
    "说明：竖线前是问题（逗号分隔，最多 2 个，最重要的在前），"
    "竖线后是严重度（1~5 的整数）。\n\n"
    "评价列表：\n{listing}\n"
)

# 提示词版本号 = 模板内容的 SHA256 前 8 位。
# 为什么要记：提示词改过 3 次（JSON 数组 → 行格式 → P4），
# 不记版本就没法回答"这批 450 条结果是哪版提示词跑出来的"，
# 也没法在改提示词后判断"变好还是变坏"。
PROMPT_HASH = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:8]


def _build_prompt(items: list[tuple[str, str]]) -> str:
    """拼提示词。items 是 [(R1, 文本), (R2, 文本), ...]。"""
    listing = "\n".join(f"[{rid}] {text}" for rid, text in items)
    return PROMPT_TEMPLATE.format(
        n=len(items), max_topics=MAX_TOPICS, listing=listing)


def _parse_lines(text: str) -> dict[str, dict] | None:
    """解析「R1: quality,size | 3」格式。

    注意：不要用 `([^|]+?) + 可选组` —— 非贪婪 + 可选会让它只匹配 1 个字符。
    这里用"贪婪 + 竖线必须有"，再对没有竖线的情况兜底。
    """
    out: dict[str, dict] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(R\d+)\s*[:：]\s*(.+)", line)
        if not m:
            continue
        rid, rest = m.group(1), m.group(2).strip()

        severity = None
        if "|" in rest:
            left, right = rest.split("|", 1)
            rest = left.strip()
            dm = re.search(r"(\d)", right)
            if dm:
                severity = int(dm.group(1))
        else:
            dm = re.search(r"(\d)\s*$", rest)     # 兜底：末尾单个数字
            if dm:
                severity = int(dm.group(1))
                rest = rest[:dm.start()].strip()

        topics = [t.strip() for t in re.split(r"[,，/、\s]+", rest) if t.strip()]
        topics = [t for t in topics if t in TOPICS][:MAX_TOPICS]
        if topics:
            out[rid] = {"topics": topics, "severity": severity}
    return out or None


def _clamp(value, lo: float, hi: float, default: float) -> float:
    """把模型给的数值压进 [lo, hi]；解析失败用默认值。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _validate(parsed: dict | None, items: list[tuple[str, str]]) -> dict[str, dict]:
    """校验解析结果，返回 {R1: {topics, severity}}。丢弃：id 不在输入里 / topic 非法。"""
    valid_ids = {rid for rid, _ in items}
    out: dict[str, dict] = {}
    for rid, data in (parsed or {}).items():
        if rid not in valid_ids:
            continue
        topics = [t for t in data.get("topics", []) if t in TOPICS][:MAX_TOPICS]
        if not topics:
            continue
        out[rid] = {
            "topics": topics,
            "severity": int(_clamp(data.get("severity"), 1, 5, 3)),
        }
    return out


def _try_batch(llm: TextLLM, pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """一次分类 N 条，返回 {rid: {topics, severity}}；失败返回空 dict。"""
    n = len(pairs)
    try:
        # 每条一行文本（约 15 token），留足余量
        raw = llm.chat(_build_prompt(pairs), max_tokens=100 + 30 * n)
    except Exception as e:                       # noqa: BLE001 上层要降级，不能抛
        print(f"  [分类] 批量调用失败：{e}")
        return {}

    parsed = _parse_lines(raw)
    if parsed is None:
        print(f"  [分类] 输出解析失败（{len(raw)} 字符）：\n"
              f"    头：{raw[:100]!r}\n"
              f"    尾：{raw[-100:]!r}")
        return {}
    return _validate(parsed, pairs)


def _to_row(record_id: str, data: dict, impl: str,
            model_version: str | None, degraded: bool) -> dict:
    """组装一行 review_topic 数据。"""
    topics = data["topics"]
    # 次要标签剔除两类噪声：
    #   "other" —— 它是兜底词（"说不清具体问题"），当次要标签没有信息量
    #   与主标签重复 —— 模型偶尔会写 "quality,quality"
    secondary = [t for t in topics[1:]
                 if t != "other" and t != topics[0]]
    return {
        "record_id": record_id,
        "topic": topics[0],                          # 主问题
        "secondary_topics": secondary,               # 次要问题（0~1 个）
        "severity": data["severity"],
        "confidence": 0.8,
        "impl": impl,
        "model_version": model_version,
        "prompt_hash": PROMPT_HASH if impl == IMPL_LLM else None,
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
    return _to_row(record_id, {"topics": [topic], "severity": 3},
                   IMPL_FALLBACK, model_version, True)


def classify_many(llm: TextLLM, items: list[dict], *, batch_size: int = 10,
                  model_version: str | None = None) -> tuple[list[dict], ClassifyStats]:
    """批量分类（三级降级 + 多标签）。

    :param items: [{"record_id": "uuid", "text": "..."}]
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

        for rid, it in id_map.items():
            if rid in parsed:
                row = _to_row(it["record_id"], parsed[rid], IMPL_LLM,
                              model_version, False)
                results.append(row)
                stats.batch_ok += 1
                if len(row["secondary_topics"]) >= 1:
                    stats.multi_label += 1

        # 漏掉/格式错的，逐条重试（②）
        for rid, it in id_map.items():
            if rid in parsed:
                continue
            single = _try_batch(llm, [(rid, _clean(it["text"]))])
            if rid in single:
                row = _to_row(it["record_id"], single[rid], IMPL_LLM,
                              model_version, False)
                results.append(row)
                stats.retry_ok += 1
                if len(row["secondary_topics"]) >= 1:
                    stats.multi_label += 1
            else:
                # ③ 关键词兜底（只能给一个主题）
                topic = infer_topic(it["text"]) or "other"
                results.append(_to_row(
                    it["record_id"], {"topics": [topic], "severity": 3},
                    IMPL_FALLBACK, model_version, True,
                ))
                stats.fallback += 1

        print(f"  批次 {stats.batches}: 批量成功 {len(parsed)}/{len(chunk)}")

    return results, stats
