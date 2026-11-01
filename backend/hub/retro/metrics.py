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


def sales_rates(spus: list[str], start: date, end: date) -> dict:
    """一组商品在窗口内的**售后/转化**指标（阶段 3.4）。

    ⚠️ **这两个数只作展示，不参与判定**（2026-11-01 的数据结论）：
        `scripts/probe_refund_corr.py` 实测 40 个 observation，
        "差评率变化"和"退款率变化"**同向只有 21/40 = 52.5%** ——
        等于抛硬币（随机 50%）✅
        根因：`generate_mock.py` 里退款是独立随机数（`gmv × uniform(0.01,0.03)`），
              和差评**没有任何耦合** ✅
        → 硬写进判定 = **用随机数判定业务效果** ✅ ✗
        详见 `docs/开发指南/16_阶段3.4_复盘指标扩展.md`
        **什么时候可以加进判定**：mock 加了耦合，或接真实平台数据后，
        重跑那个探针、同向率 > 75% 即可 ✅

    口径：
        refund_rate = 退款件数 / 支付件数   ← `sales_daily.refund_units / units`
        conversion  = **支付件数 / 访客数**  ← `units / uv`
        ⚠️ `sales_daily.conversion` 这一列**在库里全是 NULL**（2026-11-01 核对 ✅）
           → 所以**自己用 `units / uv` 算**，不要读那一列 ✅
           （读它会得到 None，然后"转化率 0%"这种假数据 ✅）
    """
    if not spus:
        return {"gmv": 0.0, "units": 0, "refund_units": 0, "refund_rate": None,
                "conversion": None, "uv": 0}
    from hub.persistence.db import get_session
    from hub.persistence.models import SalesDaily

    with get_session() as s:
        row = s.execute(
            select(func.sum(SalesDaily.gmv), func.sum(SalesDaily.units),
                   func.sum(SalesDaily.refund_units), func.sum(SalesDaily.uv))
            .where(SalesDaily.canonical_spu_id.in_(spus),
                   SalesDaily.stat_date >= start,
                   SalesDaily.stat_date < end)
        ).one()
    gmv, units, refund, uv = (float(row[0] or 0), int(row[1] or 0),
                              int(row[2] or 0), int(row[3] or 0))
    return {"gmv": gmv, "units": units, "refund_units": refund,
            "refund_rate": round(refund / units, 4) if units else None,
            "conversion": round(units / uv, 6) if uv else None,
            "uv": uv}


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
