# -*- coding: utf-8 -*-
"""
复盘编排（retro/report.py）
===========================
把 metrics + did 串起来：找出到期的动作 → 选对照组 → 算 DID → 判结论 → 写回。

**一个动作（proposal）= 一个复盘单元**：
    `action_outcome` 是"一个受影响商品一条"（配对需要），
    但 DID 的统计单位是【组】，所以这里先按 proposal 把商品聚起来。
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import ActionOutcome, ActionProposal, PromoCalendar, SpuCatalog
from hub.retro.did import DidResult, compute, judge
from hub.retro.metrics import build_input

MIN_CONTROL = 3          # 对照组至少要几个商品，少于这个数判"对照组不足"


def _all_spus() -> list[str]:
    """库里所有平台商品（对照组的候选池）。"""
    with get_session() as s:
        return list(s.scalars(select(SpuCatalog.canonical_spu_id)).all())


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _pick_control(treated: list[str], exclude: set[str]) -> list[str]:
    """对照组 = 候选池里【在整个窗口内没被任何干预覆盖】的商品。

    :param exclude: 已经被别的 outcome 覆盖的商品（窗口重叠的那些）
    """
    return [spu for spu in _all_spus()
            if spu not in exclude and spu not in treated]


def _confounds(treated: list[str], base_start: date, obs_end: date,
               other_outcomes: list[ActionOutcome]) -> list[str]:
    """列出混杂因素（不显著之外的原因）。"""
    found: list[str] = []

    # ① 重叠干预：处理组里有商品在同一窗口内被【别的提案】干预过
    for o in other_outcomes:
        if o.treat_canonical_spu_id in treated and _overlaps(
                o.baseline_start, o.observe_end, base_start, obs_end):
            found.append("重叠干预")
            break

    # ② 撞大促：窗口内有【定向覆盖本组商品】的促销
    #
    # **这一条的关键是"定向"两个字**：
    #   全店大促（spu_ids 为空）→ 处理组和对照组都在促销里 → DID 能减掉 → 不是混杂
    #   定向促销（spu_ids 命中处理组）→ 只有处理组在做活动 → 对照组减不掉 → 是混杂
    #   真实业务里"主推款才做大促"很常见，数据分不出来，**只有日历能分出来**。
    #
    #   实测踩过：第一版写成"窗口内有任何促销就算撞大促"，
    #   结果一个全店大促让 4 组全部被判 INCONCLUSIVE，M9 等于没做。
    with get_session() as s:
        promos = s.scalars(select(PromoCalendar)).all()
    for p in promos:
        if not _overlaps(p.start_date, p.end_date, base_start, obs_end):
            continue
        targets = set(p.spu_ids or [])
        if targets & set(treated):
            found.append("撞大促")
            break
    return found


def retro_one(proposal_id, outcomes: list[ActionOutcome],
              all_outcomes: list[ActionOutcome], today: date) -> dict:
    """对单个动作做复盘。返回结果 dict（不写库）。"""
    treated = [o.treat_canonical_spu_id for o in outcomes]
    first = outcomes[0]
    base_start, base_end, obs_end = (first.baseline_start, first.baseline_end,
                                     first.observe_end)
    obs_start = base_end + timedelta(days=1)

    # 对照组：排除所有"在窗口内被干预过"的商品
    covered = {o.treat_canonical_spu_id for o in all_outcomes
               if _overlaps(o.baseline_start, o.observe_end, base_start, obs_end)}
    control = _pick_control(treated, covered - set(treated))

    others = [o for o in all_outcomes if o.proposal_id != proposal_id]
    confounds = _confounds(treated, base_start, obs_end, others)
    if len(control) < MIN_CONTROL:
        confounds.append("对照组不足")

    if not control:
        return {"proposal_id": proposal_id, "status": "INCONCLUSIVE",
                "conclusion": "INCONCLUSIVE", "reason_code": "对照组不足",
                "detail": "窗口内没有任何未被干预的商品可以作对照",
                "treated": treated, "control": [], "result": None}

    data = build_input(treated, control, base_start, base_end, obs_start, obs_end)
    result: DidResult = compute(data, confounds=confounds)
    verdict = judge(result, min_control=MIN_CONTROL)
    return {"proposal_id": proposal_id,
            "treated": treated, "control": control,
            "window": (base_start, base_end, obs_start, obs_end),
            "result": result, **verdict}


def run(*, apply: bool = True, only_due: bool = True,
        today: date | None = None) -> tuple[list[dict], list[dict]]:
    """跑复盘。

    :return: (完成的, 还在等窗口的)
    """
    today = today or date.today()
    with get_session() as s:
        all_outcomes = list(s.scalars(select(ActionOutcome)).all())
        props = {p.id: p for p in s.scalars(select(ActionProposal)).all()}

    by_proposal: dict = defaultdict(list)
    for o in all_outcomes:
        by_proposal[o.proposal_id].append(o)

    done, waiting = [], []
    for pid, outcomes in by_proposal.items():
        due = max(o.observe_end for o in outcomes) < today
        if only_due and not due:
            waiting.append({"proposal_id": pid, "proposal": props.get(pid),
                            "observe_end": max(o.observe_end for o in outcomes),
                            "n_spus": len(outcomes)})
            continue
        res = retro_one(pid, outcomes, all_outcomes, today)
        res["proposal"] = props.get(pid)
        done.append(res)

    if apply:
        _write_back(done)
    return done, waiting

def _write_back(done: list[dict]) -> None:
    """把结论写回 action_outcome（同一提案的所有商品共用一份结论）。"""
    with get_session() as s:
        for d in done:
            rows = s.scalars(select(ActionOutcome)
                             .where(ActionOutcome.proposal_id == d["proposal_id"])).all()
            r = d["result"]
            for o in rows:
                o.status = "DONE"
                o.conclusion = d["conclusion"]
                o.reason_code = d.get("reason_code")
                o.confidence = (None if r is None
                                else float(min(1.0, abs(r.did_pp or 0) / 20)))
                o.metrics_before = (None if r is None else {
                    "rate": r.treated_before, "control_rate": r.control_before,
                })
                o.metrics_after = (None if r is None else {
                    "rate": r.treated_after, "control_rate": r.control_after,
                })
                o.effect = (None if r is None else {
                    "did_pp": r.did_pp, "ci_low": r.ci_low, "ci_high": r.ci_high,
                    "n_treated": r.n_treated, "n_control": r.n_control,
                    "n_reviews": r.n_reviews, "detail": d.get("detail"),
                })
                o.concluded_at = datetime.now(timezone.utc)
        s.commit()


def print_report(done: list[dict], waiting: list[dict]) -> None:
    """打印复盘报告。"""
    print("\n" + "=" * 70)
    print("  M9 效果复盘（双重差分 DID）")
    print("=" * 70)

    if not done:
        print("\n  没有到期的动作。")
    for d in done:
        p = d.get("proposal")
        r = d["result"]
        title = getattr(p, "tool_name", "?")
        print(f"\n  【{d['conclusion']}】{title}｜{len(d['treated'])} 个商品"
              f"｜理据：{d.get('reason_code')}")
        if r is not None:
            c = r.counts
            tb, cb = c["treated"], c["control"]
            print(f"    处理组：{_pct(r.treated_before)} → {_pct(r.treated_after)}"
                  f"　（{tb['bad_before']}/{tb['n_before']} → "
                  f"{tb['bad_after']}/{tb['n_after']}）")
            print(f"    对照组：{_pct(r.control_before)} → {_pct(r.control_after)}"
                  f"　（{cb['bad_before']}/{cb['n_before']} → "
                  f"{cb['bad_after']}/{cb['n_after']}）"
                  f"｜{r.n_control} 个商品")
            if r.did_pp is not None:
                print(f"    DID = {r.did_pp:+.2f}pp"
                      f"　95% 区间 [{r.ci_low:+.2f}, {r.ci_high:+.2f}]")
        print(f"    {d.get('detail')}")

    if waiting:
        print(f"\n  还在等观察窗口：{len(waiting)} 个动作")
        for w in waiting[:5]:
            print(f"    {getattr(w.get('proposal'), 'tool_name', '?')}"
                  f"｜{w['n_spus']} 个商品｜观察到 {w['observe_end']}")

    from collections import Counter
    c = Counter(d["conclusion"] for d in done)
    print(f"\n  汇总：POSITIVE {c.get('POSITIVE', 0)}｜NEGATIVE {c.get('NEGATIVE', 0)}"
          f"｜INCONCLUSIVE {c.get('INCONCLUSIVE', 0)}")
    print("=" * 70)


def _pct(x) -> str:
    return "—" if x is None else f"{x:.2%}"
