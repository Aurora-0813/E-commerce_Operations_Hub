# -*- coding: utf-8 -*-
"""
规则轨（rules.py）
=================
9 条硬规则里的 8 条（R001–R006、R008）在这里；R007（近似重复）需要整批数据，
放在 find_near_duplicates()；R009 是"保护策略"，不在这里，在 fusion.py。

为什么规则轨必须先跑、且永远可用：
    模型可能加载失败、版本不匹配，但规则是纯 Python，
    永远不会因为依赖问题而失效——它是 DQE 的"兜底"。
"""
import re
from dataclasses import dataclass

from hub.dqe.normalize import text_stats
from hub.dqe.simhash import char_bigrams

# ==================== 词库（可维护，随时增删） ====================

# R004 联系方式：手机号 / 微信 QQ 号 / URL
_CONTACT = re.compile(
    r"1[3-9]\d{9}"                                          # 手机号
    r"|(?:微信|wx|WX|vx|VX|qq|QQ)\s*[:：]?\s*[A-Za-z0-9_-]{5,20}"  # 微信号/QQ号
    r"|https?://\S+"                                         # URL
)

# R005 广告词：引流、刷单、代发
AD_WORDS = (
    "加微", "加V", "加v", "刷单", "代发", "代购", "私聊",
    "返现", "秒杀群", "招代理", "一条龙", "包过", "低价出",
)

# R006 灌水模板：整条内容等于这些词，就是灌水
TEMPLATES = (
    "此用户未填写评价", "此用户没有填写评价", "默认好评", "好评",
    "暂无评价", "系统默认", "111111", "666666", "还没评价",
)

# R008 Prompt 注入特征：试图让 LLM 忽略系统指令
INJECTION_PATTERNS = (
    "忽略以上", "忽略之前", "忽略前面", "忽略指令",
    "ignore previous", "ignore above", "ignore all previous",
    "forget your instructions", "disregard", "system:",
    "你现在是", "现在开始你是",
)


# ==================== 规则命中结果 ====================

@dataclass(frozen=True)
class RuleHit:
    """一条规则命中。"""
    rule_id: str          # "R001"
    desc: str             # 人话描述
    downgradable: bool = True
    # 差评（rating ≤ 2）时，这条命中能不能降级为 review。
    # 只有 R008（Prompt 注入）是 False——注入必须硬 drop，防攻击面。


def check_item(content: str, has_image: bool) -> list[RuleHit]:
    """检查**单条**评价，返回命中的规则（R001–R006、R008）。

    注意：这里**不做处置决策**（drop / review），只回答"命中了哪些规则"。
    怎么处置交给 fusion.py——因为处置要看 rating（R009 差评保护）。

    **检查顺序有讲究**：越具体的规则越先查，
    这样 hits[0] 就是最具体的原因（写进 reason_code），而不是"文本太短"这种笼统原因。
    顺序：R008（攻击）→ R006（模板）→ R004（联系方式）→ R005（广告）→ R001/R002/R003（笼统）

    :param content:   归一化后的正文
    :param has_image: 是否带图（R001/R002 在带图时不判）
    """
    hits: list[RuleHit] = []
    stats = text_stats(content)

    # ---------- 先查"最具体"的规则 ----------

    # R008：Prompt 注入（大小写不敏感，中英都查）
    # 唯一"不可降级"的规则：差评命中 R008 也照样 drop，防攻击面
    lowered = content.lower()
    if any(p in lowered for p in INJECTION_PATTERNS):
        hits.append(RuleHit("R008", "Prompt 注入", downgradable=False))

    # R006：灌水模板（整条内容恰好等于模板词，不是"包含"）
    if content.strip() in TEMPLATES:
        hits.append(RuleHit("R006", "灌水模板"))

    # R004：联系方式（手机 / 微信 QQ 号 / URL）
    if _CONTACT.search(content):
        hits.append(RuleHit("R004", "含联系方式"))

    # R005：广告词
    if any(w in content for w in AD_WORDS):
        hits.append(RuleHit("R005", "含广告词"))

    # ---------- 再查"笼统"的规则 ----------

    # R001：文本长度 < 5 且无图
    # 为什么"带图不判"：一张图可能本身就是有效证据（多模态会去读它）
    if stats["length"] < 5 and not has_image:
        hits.append(RuleHit("R001", "文本长度<5且无图片"))

    # R002：中文占比为 0 且无图（纯英文/乱码，常见于机翻刷单）
    if stats["chinese_ratio"] == 0 and not has_image:
        hits.append(RuleHit("R002", "中文占比为0且无图片"))

    # R003：标点/表情占比 > 80%（纯符号刷屏）
    if stats["punct_ratio"] > 0.8:
        hits.append(RuleHit("R003", "标点/表情占比>80%"))

    return hits


def find_near_duplicates(
    texts: list[str],
    times: list | None = None,
    threshold: float = 0.6,
    window_hours: float = 24.0,
) -> list[bool]:
    """R007：标记一批文本里，哪些是"重复的后来者"（保留第一次出现）。

    为什么要"批"：R007 判的是"同一批次内有没有人复制粘贴"，
    单看一条评价是判不出来的。

    为什么还要"时间窗"（times 参数）：
        真实的刷屏是**集中爆发**的（几条到几十条，集中在几分钟到几小时）；
        而"不同顾客用了相似说法"是**散布在几个月里**的。
        不加时间窗，会把"同一模板换商品"的正常评价也判成重复——
        实测在模板生成的 mock 数据上，误杀率高达 80%。

    :param texts:        同批次的归一化正文（顺序 = 保留优先级，先出现的留下）
    :param times:        对应的评价时间；给了就只在时间窗内比较（None = 不限制）
    :param threshold:    Jaccard 阈值，≥ 它算重复（默认 0.6）
    :param window_hours: 时间窗（小时），默认 24h
    :return:             与 texts 等长的 bool 列表，True = 该条判重复应 drop
    """
    n = len(texts)
    dup = [False] * n
    # 预计算 bigram 集合，避免两两比较时反复切分
    sets = [char_bigrams(t) for t in texts]
    sizes = [len(s) for s in sets]
    window_s = window_hours * 3600 if times is not None else None

    for i in range(n):
        if dup[i]:
            continue
        for j in range(i):          # 只和"更早的"比，保留首条
            if dup[j]:
                continue            # 已被判重的不用再当参照

            # 时间窗过滤：隔太远的不算"同一次刷屏"
            if window_s is not None and times is not None:
                gap = abs((times[i] - times[j]).total_seconds())
                if gap > window_s:
                    continue

            # 完全相同：最快路径
            if texts[i] == texts[j]:
                dup[i] = True
                break
            # 快速排除：Jaccard ≤ min(|A|,|B|) / max(|A|,|B|)，
            # 数量比都达不到阈值，就不必真算交集
            ni, nj = sizes[i], sizes[j]
            if ni == 0 or nj == 0:
                continue
            if min(ni, nj) / max(ni, nj) < threshold:
                continue
            inter = len(sets[i] & sets[j])
            union = ni + nj - inter
            if union and inter / union >= threshold:
                dup[i] = True
                break
    return dup


# 规则优先级：数字越小越优先（越具体）。
# 用于把 R007（engine 里批量判重后补进来的）和其他规则放在一起排序，
# 保证 hits[0] 永远是最能说明问题的原因。
RULE_PRIORITY = {
    "R008": 0,   # 攻击，最高
    "R007": 1,   # 批量近似重复
    "R006": 2,   # 灌水模板
    "R004": 3,   # 联系方式
    "R005": 4,   # 广告词
    "R001": 5,   # 太短
    "R002": 6,   # 非中文
    "R003": 7,   # 纯符号
}


def sort_hits(hits: list[RuleHit]) -> list[RuleHit]:
    """按优先级排序（越具体越靠前）。"""
    return sorted(hits, key=lambda h: RULE_PRIORITY.get(h.rule_id, 99))
