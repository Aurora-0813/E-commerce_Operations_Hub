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
    "- desc_mismatch  描述不符：实物颜色/面料/厚薄**和页面写的不一样**\n"
    "- misship        错发漏发：**发错颜色/尺码/款式**、漏发、少发\n"
    "- other          抱怨了但说不清具体问题\n\n"
    "输出格式（每条一行，不要其他内容）：\n"
    "R1: quality,size | 3\n"
    "R2: logistics | 2\n\n"
    "说明：竖线前是问题（逗号分隔，最多 2 个，最重的在前），竖线后是严重度（1~5）。\n\n"
    "评价列表：\n{listing}\n"
)

# ============ 两个被数据否掉的尝试（记录在此，避免以后重复踩）============
#
# ① 【加"区分提到与抱怨"的规则】→ 净负收益，已撤
#    动机：错例 "发货慢…质量对得起价格" 被判成 quality（好评词触发）
#    做法：在提示词里加"…质量对得起价格 是好评，不算 quality 问题"
#    结果：95.8% → 93.1%（4 轮确定性复现）
#          desc_mismatch 从 3/3 掉到 1/3，misship 从 8/8 掉到 7/8
#          而它想修的那条**根本没修好**
#    原因：把"颜色很正/颜色比图片好看"举例成"好评"，
#          模型开始回避【所有带颜色的句子】→ 色差/发错颜色反而判错。
#    ⚠️ 教训：给模型举反例时，反例涉及的**关键词会连带被压制**。
#
# ② 【要求模型回显原文片段，用于检测串行】→ 代价不划算，已撤
#    结果：耗时 215s → 318s（+48%），收益是抓到约 1% 的串行
#          而且它自己引入新失败模式（模型漏写某一栏 → 整批报废）
#    ⚠️ 但那次实验意外发现了一件**更有价值的事**：
#          回显校验误把 76 条打回逐条重试 → 准确率 97.2%
#          → **单条分类比批量分类更准**（见下方 batch_size 的说明）
#
# 【确定性的事实】：同一提示词连跑 3 次，准确率/多标签/耗时完全一致
#    （scripts/measure_noise.py 实测 93.1% / 93.1% / 93.1%）
#    → 所以"提示词改动导致的准确率变化"是**可信的**，不是噪声。
#    → 之前把它当噪声，是误判。

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


def _norm(s: str) -> str:
    """归一化后再做"子串在不在原文里"的判断。

    为什么需要：模型回显时常用的标点和原文不一定一致——
        模型：'版型有问题, 肩宽对不上'（半角逗号 + 空格）
        原文：'版型有问题，肩宽对不上'（全角逗号）
        直接 `in` 判断会失败 → 把正确的标签当串行剔除。
    NFKC 会把全角标点转半角；再去掉所有空白。
    """
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _parse_lines(text: str) -> dict[str, dict] | None:
    """解析「R1|quality,size|3|回显」格式（也兼容旧格式和缺回显的 3 段格式）。

    **格式设计的关键**：必要字段在前（问题、严重度），**校验字段（回显）放在最后**。

        为什么不能把回显放中间：
            2026-10 实测，模型约 9% 的批次会漏掉中间那一栏，
            输出 `R1|回显|2` —— 整行报废 → 整批 0/10 → 10 条好数据被格式抖动带走。
            而且重试没用：temperature=0，同样的提示词必然给同样的错输出。

        放到最后之后：
            漏了回显 → `R1|quality|2` → 前两段还在 → 分类可用 ✅（只是无法校验）
            有回显   → 三者齐全 → 顺便核对有没有串行 ✅
    """
    out: dict[str, dict] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(R\d+)\s*([:：|])\s*(.+)", line)
        if not m:
            continue
        rid, sep, rest = m.group(1), m.group(2), m.group(3).strip()

        if sep == "|":
            parts = [p.strip() for p in rest.split("|")]
            if len(parts) < 2:
                continue
            # parts[0]=问题, parts[1]=严重度, parts[2]（可选）=回显
            topics_raw = parts[0]
            try:
                severity = int(re.search(r"(\d)", parts[1]).group(1))
            except (AttributeError, TypeError):
                severity = None
            quote = parts[2] or None if len(parts) >= 3 else None
        else:
            # 旧格式：`R1: quality,size | 3`
            topics_raw, quote = rest, None
            severity = None
            if "|" in rest:
                left, right = rest.split("|", 1)
                topics_raw = left.strip()
                dm = re.search(r"(\d)", right)
                if dm:
                    severity = int(dm.group(1))
            else:
                dm = re.search(r"(\d)\s*$", rest)
                if dm:
                    severity = int(dm.group(1))
                    topics_raw = rest[:dm.start()].strip()

        topics = [t.strip() for t in re.split(r"[,，/、\s]+", topics_raw) if t.strip()]
        topics = [t for t in topics if t in TOPICS][:MAX_TOPICS]
        if topics:
            out[rid] = {"topics": topics, "severity": severity, "quote": quote}
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
            # ⚠️ quote 必须透传！第一版这里只保留了 topics/severity，
            # 结果 _try_batch 拿到 quote=None → 把所有正确的标签都当"串行"剔除了。
            "quote": data.get("quote"),
        }
    return out


def _try_batch(llm: TextLLM, pairs: list[tuple[str, str]],
               require_quote: bool = False) -> dict[str, dict]:
    """一次分类 N 条，返回 {rid: {topics, severity}}；失败返回空 dict。

    :param require_quote: True 时强制核对"回显片段确实是这一条的原文"。
        **批量调用必须开**（防行号错位）；单条调用不用开
        （只有一条，不可能串行；而且强制回显会让"模型没写回显"的正确答案被丢掉）。
    """
    n = len(pairs)
    text_by_rid = dict(pairs)
    try:
        # 每条一行文本（约 15 token + 回显），留足余量
        raw = llm.chat(_build_prompt(pairs), max_tokens=120 + 40 * n)
    except Exception as e:                       # noqa: BLE001 上层要降级，不能抛
        print(f"  [分类] 批量调用失败：{e}")
        return {}

    parsed = _parse_lines(raw)
    if parsed is None:
        print(f"  [分类] 输出解析失败（{len(raw)} 字符）：\n"
              f"    头：{raw[:100]!r}\n"
              f"    尾：{raw[-100:]!r}")
        return {}
    before = len(parsed)
    parsed = _validate(parsed, pairs)
    if require_quote and before != len(parsed):
        print(f"  [分类] 校验后丢弃 {before - len(parsed)} 条（主题为空/严重度非法）")

    if require_quote:
        # 核对"回显片段属于这一条"：对不上说明模型串了行（合并两行后重新编号）。
        # ⚠️ 回显是**可选**字段：没写就**不校验、保留分类结果**
        #    （宁可少一次校验，也不要因为模型偷懒把正确答案丢掉）。
        kept, dropped, unverified = {}, [], 0
        for rid, data in parsed.items():
            q = _norm(data.get("quote") or "")
            if not q:
                kept[rid] = data
                unverified += 1
            elif q in _norm(text_by_rid.get(rid, "")):
                kept[rid] = data
            else:
                dropped.append(rid)
        if dropped:
            print(f"  [分类] 回显对不上原文，剔除 {len(dropped)} 条"
                  f"（疑似串行：{','.join(dropped[:5])}）→ 转逐条重试")
        if unverified:
            print(f"  [分类] {unverified}/{len(parsed)} 条没写回显 —— "
                  f"保留分类，但这几条无法校验串行")
        return kept
    return parsed


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
        # require_quote=False：提示词不再要求回显，所以不校验串行
        # （解析器仍兼容带回显的格式，将来换更强的模型可以直接打开）
        parsed = _try_batch(llm, pairs)

        # ①.5 缺失的**重新组批**再试一次
        #
        # 为什么要有这一层：2026-10 实测，45 个批次里有 4 个整批失败
        # （模型偶尔漏掉"问题"那一段，输出 `R1|回显|严重度`）。
        # 如果直接落到"逐条重试"，40 条 × 4 秒 = 160 秒——**一次格式抖动罚 2.7 分钟**。
        # 重新组批只要 1 次调用，而且模型第二次往往就写对了（格式抖动是随机的）。
        missing = [rid for rid in id_map if rid not in parsed]
        if missing and parsed:
            retry_pairs = [(rid, txt) for rid, txt in pairs if rid in missing]
            stats.batches += 1
            again = _try_batch(llm, retry_pairs)
            parsed.update(again)

        for rid, it in id_map.items():
            if rid in parsed:
                row = _to_row(it["record_id"], parsed[rid], IMPL_LLM,
                              model_version, False)
                results.append(row)
                stats.batch_ok += 1
                if len(row["secondary_topics"]) >= 1:
                    stats.multi_label += 1

        # 漏掉/格式错/回显对不上的，逐条重试（②）
        # 逐条时不开 require_quote：只有一条，不可能串行
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
