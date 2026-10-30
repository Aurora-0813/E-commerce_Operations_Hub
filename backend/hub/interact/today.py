# -*- coding: utf-8 -*-
"""
今日工作台（interact/today.py）
===============================
**回答一个问题："我现在该做什么？"**

和 `hub status` 的区别不是排版，是**有没有行动**：

    hub status  → "现在是什么样"     每一行都是事实，不需要你动
    hub today   → "我现在该做什么"   每一行都必须带一个【下一步】

## 只允许两种"等"

设计原则（**这一条决定了整个模块的形态**）：

    系统里只允许两种"等"：
        等人批     → 有主（运营）
        等时间     → 有主（到期就动）
    第三类"等"——**没有主**——不该存在，出现了就是异常：

        · P0/P1 没出提案     → 异常（应该本轮就出）
        · 队列里有 FAILED    → 异常（应该死信告警）
        · 死信没人核         → 异常（应该有人跟进）

    所以 `today()` 里，**"有主"的给分组，"没主"的给告警**。

## 排序原则：按"该不该我现在动手"

    不按时间排、不按分数排，按【责任归属】排：
        🔴 要我做（现在）  → 待批：不批就卡住
        🟡 该做（今天）    → 到期的复盘：算完才知道效果
        🔵 知会（不用动）  → 等窗口：做了，还没到时间
        ⚠️ 异常（要修）    → 没主的东西
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select

from hub.persistence.db import get_session
from hub.persistence.models import (
    ActionOutcome,
    ActionProposal,
    DeadLetter,
    HitlTask,
    PipelineRun,
)
from hub.persistence.repo_cluster import ClusterRepo


@dataclass
class Item:
    """一条待办。**必须有 next_step**，否则它只是一条报表。"""
    text: str
    next_step: str
    detail: str = ""


@dataclass
class Today:
    must_do: list[Item] = field(default_factory=list)     # 🔴 要我做
    should_do: list[Item] = field(default_factory=list)   # 🟡 该做
    waiting: list[Item] = field(default_factory=list)     # 🔵 知会
    anomalies: list[Item] = field(default_factory=list)   # ⚠️ 没主的东西

    @property
    def total_actions(self) -> int:
        """需要人真正动手的条数。0 = 今天没事。"""
        return len(self.must_do) + len(self.should_do) + len(self.anomalies)


def collect() -> Today:
    """汇总"今天该做什么"。纯聚合，不调 LLM。"""
    t = Today()
    today = date.today()
    with get_session() as s:
        # ---------- 🔴 待批：不批就卡住 ----------
        tasks = s.scalars(select(HitlTask).where(HitlTask.status == "PENDING")).all()
        for task in tasks:
            p = s.get(ActionProposal, task.proposal_id)
            label = (p.rationale or "")[:24] if p else ""
            tool = p.tool_name if p else "?"
            risk = p.risk_level if p else "?"
            t.must_do.append(Item(
                text=f"[{risk}] {label} → {tool}",
                next_step=f"hub approve {str(task.id)[:8]}",
                detail=f"等待中（{task.created_at:%Y-%m-%d}）",
            ))

        # ---------- 🟡 到期该复盘 ----------
        # 观察窗口已经走完、但还没算效果的
        due = s.scalars(
            select(ActionOutcome)
            .where(ActionOutcome.status == "WAITING",
                   ActionOutcome.observe_end < today)
        ).all()
        if due:
            by_proposal = {o.proposal_id for o in due}
            t.should_do.append(Item(
                text=f"{len(by_proposal)} 个动作的观察窗口已到期，还没算效果",
                next_step="hub retro",
                detail=f"涉及 {len(by_proposal)} 个提案",
            ))

        # ---------- 🔵 等窗口（知会，不需要动作）----------
        waiting = s.scalars(
            select(ActionOutcome)
            .where(ActionOutcome.status == "WAITING",
                   ActionOutcome.observe_end >= today)
        ).all()
        if waiting:
            earliest = min(o.observe_end for o in waiting)
            by_proposal = {o.proposal_id for o in waiting}
            t.waiting.append(Item(
                text=f"{len(by_proposal)} 个动作在等观察窗口",
                next_step="（不用动，到期会自动出现在上面）",
                detail=f"最早 {earliest} 出结果",
            ))

        # ---------- ⚠️ 异常：没主的东西 ----------
        # ① P0/P1 漏出（该出提案却【连一条记录都没有】）
        #
        # ⚠️ 这里踩过一个坑：最早我用 `args["cluster_id"]` 判断"有没有提案"，
        #    结果 `listing.update_desc` 的 args 是 spu_id、没有 cluster_id，
        #    于是 2 个明明有提案的簇被误报成"没有提案"。
        #    正确的关系在 `evidence_refs` 里（那里一直写着 issue_cluster）。
        #    **教训：判断关系要用【专门记录关系的地方】，不要从业务参数里猜。**
        clusters = ClusterRepo().fetch_latest_clusters()
        p0p1 = [c for c in clusters if (c.priority_tier or "") in ("P0", "P1")]
        covered: set[str] = set()
        skipped: dict[str, str] = {}
        for p in s.scalars(select(ActionProposal)).all():
            for ref in (p.evidence_refs or []):
                cid = ref.get("issue_cluster") if isinstance(ref, dict) else None
                if not cid:
                    continue
                covered.add(str(cid))
                if p.status == "SKIPPED":
                    skipped[str(cid)] = (p.rationale or "")[:40]
        missing = [c for c in p0p1 if str(c.id) not in covered]
        if missing:
            t.anomalies.append(Item(
                text=f"⚠️ {len(missing)} 个 P0/P1 簇【连跳过记录都没有】（异常）",
                next_step="hub propose",
                detail="、".join(c.label for c in missing[:3]),
            ))
        # 跳过的也是"有去向"，但运营应该看得到原因（不是悄悄消失）
        if skipped:
            t.waiting.append(Item(
                text=f"{len(skipped)} 个 P0/P1 簇被跳过（有原因，可复核）",
                next_step="hub status",
                detail="；".join(list(skipped.values())[:2]),
            ))

        # ② 队列里有失败的链
        failed = s.scalar(select(func.count()).select_from(PipelineRun)
                          .where(PipelineRun.status.in_(("FAILED", "DEAD")))) or 0
        if failed:
            t.anomalies.append(Item(
                text=f"⚠️ {failed} 条流水线失败/死信",
                next_step="hub queue --dlq",
                detail="需要人工核对后才能重放",
            ))

        # ③ 死信没人核
        n_dead = s.scalar(select(func.count()).select_from(DeadLetter)) or 0
        if n_dead:
            t.anomalies.append(Item(
                text=f"⚠️ {n_dead} 条死信待人工核对",
                next_step="hub queue --dlq",
            ))

    return t


def print_today(t: Today) -> None:
    """打印工作台。空的时候也要给一行摘要，否则运营会以为系统坏了。"""
    print("\n" + "=" * 66)
    print("  今天该做什么")
    print("=" * 66)

    if not t.total_actions and not t.waiting:
        print("\n  ✅ 没有待办。")
        print("\n  （如果觉得不该为空，用 `hub status` 看现状、"
              "`hub health` 看最近一次运行）")
        print("=" * 66)
        return

    def _section(title: str, items: list[Item]) -> None:
        if not items:
            return
        print(f"\n{title}")
        for i, it in enumerate(items, 1):
            print(f"  {i}. {it.text}")
            print(f"     → {it.next_step}")
            if it.detail:
                print(f"       {it.detail}")

    _section("🔴 要我做（不批就卡住）", t.must_do)
    _section("🟡 该做（做完才知道效果）", t.should_do)
    _section("🔵 知会（做了，等结果）", t.waiting)
    _section("⚠️ 异常（没有主的东西，要修）", t.anomalies)

    print("\n" + "-" * 66)
    n = t.total_actions
    print(f"  需要你动手：{n} 件" if n else "  今天不需要你动手")
    print("=" * 66)
