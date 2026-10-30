# -*- coding: utf-8 -*-
"""
复盘指标（retro/metrics.py）
===========================
算"一组商品在某个时间窗内的差评率"。

**按商品拿到 (总数, 差评数)，而不是直接给一个比率**——为什么：
    bootstrap 需要重抽样。而重抽样的单位必须是【商品】，不能是【评价】。
    同一条评价在同一个商品下并不独立（一个商品的差评往往成堆出现，
    比如同一批面料出问题），按评价重抽样会把置信区间算得过窄（假显著）。
    这是 DID 里最容易搞错的地方之一。
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import case, func, select

from hub.persistence.db import get_session
from hub.persistence.models import Record as RecordRow

# 差评 = 1~2 星（和 M3 的 `rating_max=2` 保持一致，改一处要改两处）
BAD_RATING_MAX = 2


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """把日期区间变成 [start 00:00, end+1 00:00) 的 UTC 时间边界。"""
    return (datetime.combine(start, time.min, tzinfo=timezone.utc),
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc))


def per_spu_counts(spus: list[str], start: date, end: date) -> dict[str, dict]:
    """一组商品在窗口内的 (评价数, 差评数)。

    :return: {商品ID: {"n": 12, "bad": 3}}

    口径说明（很重要，写进文档）：
        · 只统计 `dqe_decision='keep'` 的评价 —— 刷单/灌水是假数据，
          它们会稀释差评率，而且"有没有被洗掉"本身和干预无关。
        · 差评 = rating <= 2（和 M3 分类的筛选条件一致）。
        · 用 `platform_spu_id` 匹配，**不用** `canonical_spu_id`：
          后者是 M11 同款对齐的结果（可能为空），而 action_outcome 里存的是平台 ID。
    """
    out = {spu: {"n": 0, "bad": 0} for spu in spus}
    if not spus:
        return out
    lo, hi = _bounds(start, end)
    is_bad = case((RecordRow.rating <= BAD_RATING_MAX, 1), else_=0)
    with get_session() as s:
        rows = s.execute(
            select(RecordRow.platform_spu_id, func.count(), func.sum(is_bad))
            .where(RecordRow.platform_spu_id.in_(spus),
                   RecordRow.dqe_decision == "keep",
                   RecordRow.review_time >= lo,
                   RecordRow.review_time < hi)
            .group_by(RecordRow.platform_spu_id)
        ).all()
    for spu, n, bad in rows:
        out[spu] = {"n": int(n or 0), "bad": int(bad or 0)}
    return out


@dataclass
class DidInput:
    """DID 的输入：处理组和对照组各自的前/后计数（按商品）。"""
    treated: dict[str, dict]     # {spu: {"n_before","bad_before","n_after","bad_after"}}
    control: dict[str, dict]

    @property
    def n_treated(self) -> int:
        return len(self.treated)

    @property
    def n_control(self) -> int:
        return len(self.control)


def build_input(treated: list[str], control: list[str],
                base_start: date, base_end: date,
                obs_start: date, obs_end: date) -> DidInput:
    """把处理组/对照组在"前窗口"和"后窗口"的计数拼起来。"""
    t_before = per_spu_counts(treated, base_start, base_end)
    t_after = per_spu_counts(treated, obs_start, obs_end)
    c_before = per_spu_counts(control, base_start, base_end)
    c_after = per_spu_counts(control, obs_start, obs_end)

    def _merge(before: dict, after: dict) -> dict[str, dict]:
        out = {}
        for spu in set(before) | set(after):
            b, a = before.get(spu, {}), after.get(spu, {})
            out[spu] = {"n_before": b.get("n", 0), "bad_before": b.get("bad", 0),
                        "n_after": a.get("n", 0), "bad_after": a.get("bad", 0)}
        return out

    return DidInput(treated=_merge(t_before, t_after),
                    control=_merge(c_before, c_after))


def aggregate(counts: dict[str, dict]) -> dict:
    """把按商品的计数加总成组指标。"""
    n_before = sum(c["n_before"] for c in counts.values())
    bad_before = sum(c["bad_before"] for c in counts.values())
    n_after = sum(c["n_after"] for c in counts.values())
    bad_after = sum(c["bad_after"] for c in counts.values())
    return {
        "n_before": n_before, "bad_before": bad_before,
        "n_after": n_after, "bad_after": bad_after,
        "rate_before": bad_before / n_before if n_before else None,
        "rate_after": bad_after / n_after if n_after else None,
    }
