# -*- coding: utf-8 -*-
"""
把"历史干预"写进数据库（seed_history.py）
==========================================
`generate_mock.py` 在**数据层面**埋了 4 组历史干预（干预日之后差评率变化）。
这个脚本负责在**业务层面**把它们补全：

    ① 每组的 action_proposal（时间戳 = 干预日，这样看历史才连贯）
    ② 每组的 action_outcome（每个受影响商品一条，前 14 天 / 后 14 天窗口）
    ③ promo_calendar 里的大促（M9 判断"撞大促"的依据）

**为什么是"4 组"而不是"6 个商品"**：
    DID 的统计单位是【组】。每商品每 14 天窗口只有约 21 条评价、6 条差评，
    测不出 35% 的下降（差值标准误约 13 个百分点）；
    按组聚合（5 个标题 = 10 个平台 SKU → 215 条评价 / 58 条差评）才测得出来。
    `action_outcome` 仍然一个商品一条——那是【证据】，统计在组层面聚合。

**它和真实动作的区别**：这是"补录历史"，所以 `tool_execution` 是空的
（那些动作发生在系统上线之前）。M9 的复盘逻辑不能假设 tool_execution 一定有记录。

运行（在 backend/ 目录下）：
    python scripts/generate_mock.py       # 生成数据（含 historical_interventions.csv）
    python scripts/run_all.py             # 导入 + 全链（会 reset 数据库）
    python scripts/seed_history.py        # 补录 4 组历史干预（必须在 run_all 之后）
"""
import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import delete, func, select                    # noqa: E402

from hub.action.outcome import BASELINE_DAYS, OBSERVE_DAYS  # noqa: E402
from hub.persistence.db import get_session                  # noqa: E402
from hub.persistence.models import (                        # noqa: E402
    ActionOutcome,
    ActionProposal,
    AnalysisRun,
    PromoCalendar,
    Record as RecordRow,
    ReviewTopic,
    SpuCatalog,
)

HISTORY_CSV = _BACKEND_DIR.parent / "data" / "mock" / "historical_interventions.csv"
PROMO_NAME = "秋季保暖专场"


def _ts(d: date) -> datetime:
    """把日期变成"当天中午"的 UTC 时间（避免时区把日期挪一天）。"""
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


def _load_rows() -> list[dict]:
    with open(HISTORY_CSV, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["组"] != "CONTROL"]


def _dominant_topic(spus: list[str]) -> str:
    """这组商品的最主要问题类型（成员评价里出现最多的 topic）。

    为什么需要：经验回灌按 (topic, 工具) 统计战绩，
    而这 4 个历史动作是 spu 级、没有 cluster_id，反推不出 topic。
    生成时算一次、写进提案，下游就不用猜。
    """
    if not spus:
        return ""
    with get_session() as s:
        rows = s.execute(
            select(ReviewTopic.topic,
                   func.count())
            .join(RecordRow, RecordRow.id == ReviewTopic.record_id)
            .where(RecordRow.platform_spu_id.in_(spus))
            .group_by(ReviewTopic.topic)
            .order_by(func.count().desc())
        ).all()
    return rows[0][0] if rows else ""


def _spus_of(titles: list[str]) -> list[str]:
    """按标题找平台商品 ID（一个标题在淘宝/京东各一个）。

    ⚠️ 用 `LIKE '%标题'` 而不是 `==`：
        CSV 里写的是**裸标题**（"夏季碎花连衣裙…"），
        而 `spu_catalog.title` 存的是**带品牌的全称**（"森马 夏季碎花连衣裙…"）。
        精确匹配永远不成立——实测踩过，4 组全部"找不到商品"。
    """
    out: list[str] = []
    with get_session() as s:
        for t in titles:
            rows = s.scalars(
                select(SpuCatalog).where(SpuCatalog.title.like(f"%{t}"))
            ).all()
            out.extend(r.canonical_spu_id for r in rows)
    return out


def main() -> None:
    if not HISTORY_CSV.exists():
        print(f"❌ 找不到 {HISTORY_CSV}，先跑 python scripts/generate_mock.py")
        return
    rows = _load_rows()
    print(f"读到 {len(rows)} 组历史干预\n")

    # 幂等：先清掉上次补录的（run → proposal → outcome 现在是全链 CASCADE）
    #
    # ⚠️ 这里原来踩过四次坑：子表外键没设 ondelete，删父表就 FK 违规。
    #    前三次（review_reply_draft / hitl_task / action_outcome）都是"先显式删子表"打补丁，
    #    第四次才承认问题在 schema：**所有从表都应该 CASCADE**。
    #    现在 models.py 里三处外键都补了 ondelete="CASCADE"，这里可以放心删父表。
    with get_session() as s:
        old = s.scalars(select(AnalysisRun)
                        .where(AnalysisRun.trace_id.like("history-%"))).all()
        for run in old:
            s.delete(run)
        s.commit()
    with get_session() as s:
        s.execute(delete(PromoCalendar).where(PromoCalendar.name == PROMO_NAME))
        s.commit()
    print("已清掉上次补录的记录\n")

    ok, skipped = 0, []
    promo_spus: list[str] = []
    for row in rows:
        gname = row["组"]
        titles = [t for t in row["商品标题"].split(";") if t]
        applied = date.fromisoformat(row["干预日"])
        action = row["动作"]
        spus = _spus_of(titles)
        if not spus:
            skipped.append(f"{gname}（库里找不到这组商品）")
            continue

        # ① proposal + run（时间戳 = 干预日）
        #
        # `args["topic"]` 必须写：经验回灌（experience.py）按 (topic, 工具) 统计战绩。
        # 不写的话这 4 个历史动作对回灌完全不可见——它们是 spu 级动作，
        # 没有 cluster_id，下游反推不出 topic。
        group_topic = _dominant_topic(spus)
        trace_id = f"history-{uuid4().hex[:10]}"
        with get_session() as s:
            run = AnalysisRun(trace_id=trace_id, tenant_id="default", status="DONE",
                              stats={"module": "history"},
                              created_at=_ts(applied), started_at=_ts(applied),
                              finished_at=_ts(applied))
            s.add(run)
            s.flush()
            proposal = ActionProposal(
                run_id=run.id, trace_id=trace_id,
                tool_name=action, tool_version="1.0.0",
                args={"spu_id": spus[0], "legacy": True,
                      "group": gname, "spus": spus},
                topic=group_topic,
                risk_level="L1",
                evidence_refs=[], rationale=f"历史干预 {gname}：{row['备注']}",
                dry_run=False, status="EXECUTED",
                created_at=_ts(applied),
            )
            s.add(proposal)
            s.flush()
            pid = proposal.id
            s.commit()

        # ② outcome：每个受影响商品一条
        with get_session() as s:
            for spu in spus:
                s.add(ActionOutcome(
                    proposal_id=pid, tenant_id="default",
                    treat_canonical_spu_id=spu, control_spu_ids=[],
                    baseline_start=applied - timedelta(days=BASELINE_DAYS),
                    baseline_end=applied - timedelta(days=1),
                    observe_end=applied + timedelta(days=OBSERVE_DAYS),
                    status="WAITING",
                    created_at=_ts(applied),
                ))
            s.commit()

        ok += 1
        print(f"  ✅ {gname}｜{action}｜{applied} → {len(spus)} 个商品"
              f"｜{row['备注']}")
        if row.get("撞大促") == "是":
            promo_spus = spus

    # ③ 大促日历（M9 判断"撞大促"的依据）
    # **spu_ids 必须填**：这是"定向促销"，只覆盖 G4 那 10 个商品。
    #   如果留空（= 全店大促），M9 会认为对照组也在促销里 → DID 能减掉 → 不算混杂。
    #   而这条数据的目的就是制造一个"看起来有效、其实是大促"的假象。
    with get_session() as s:
        s.add(PromoCalendar(name=PROMO_NAME,
                            start_date=date(2026, 9, 21),
                            end_date=date(2026, 10, 4),
                            platforms=["taobao", "jd"],
                            spu_ids=promo_spus))
        s.commit()

    print(f"\n补录完成：{ok} 组")
    for x in skipped:
        print(f"  ⏭ {x}")
    print(f"大促日历：{PROMO_NAME} 2026-09-21~2026-10-04")
    print("\n下一步：hub retro（M9 算 DID）")


if __name__ == "__main__":
    main()
