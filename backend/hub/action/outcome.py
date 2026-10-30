# -*- coding: utf-8 -*-
"""
动作效果登记（outcome.py）—— M9 的前置
=======================================
**动作执行成功时**，登记"这个动作要等一个观察窗口"，而不是现在就判断效果。

    为什么不能现在判断：
        动作 5 分钟前才做，后窗口（14 天）还没到，数据里什么变化都没有。
        "动作 → 效果"需要【时间】，所以只能先挂一个 WAITING，到点再算。

**这个模块只做三件事**：
    ① 算窗口边界（前 14 天 / 后 14 天）—— 把约定钉进数据，不散在代码里
    ② 找出受影响的商品（一个动作可能影响多个）
    ③ 写入 action_outcome（status=WAITING）

**它不算效果**——DID 在 M9 的 `hub retro` 里算。

⚠️ 一个设计要点：**一个受影响的商品 = 一条 outcome**
    回复一个簇会覆盖几十个商品。DID 的单位是"处理商品 vs 对照商品"的**配对**，
    所以必须一个商品一条，否则没法配对、也没法回答"对哪个商品更有效"。
"""
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import ActionOutcome, IssueCluster


# 观察窗口：前 14 天做基线、后 14 天看变化
# **改这两个数要同时改文档**（docs/开发思路.md 第 14 步），否则结论口径不一致
BASELINE_DAYS = 14
OBSERVE_DAYS = 14


def _target_spus(args: dict) -> list[str]:
    """从工具参数里找出【受影响的商品】。

    三种情况：
        args 有 spu_id       → 就那一个（改描述、改价）
        args 有 cluster_id   → 该簇覆盖的所有商品（回复一类差评）
        都没有                → 影响面不明确，不登记
    """
    if args.get("spu_id"):
        return [str(args["spu_id"])]

    cluster_id = args.get("cluster_id")
    if cluster_id:
        try:
            with get_session() as s:
                cluster = s.get(IssueCluster, UUID(str(cluster_id)))
                return list(cluster.spu_ids or []) if cluster else []
        except (ValueError, AttributeError):
            return []

    return []


def register(proposal_id: str, spec, args: dict, tenant_id: str = "default",
             applied_on: date | None = None) -> int:
    """动作执行成功后登记"等观察窗口"。返回登记了几条。

    :param spec: ToolSpec —— 它的 `outcome_metrics` 为空就不登记
                 （只读工具 L0 没有"效果"可言）
    :param applied_on: 干预日期，默认今天（测试时可以传历史日期）
    """
    if not proposal_id or not spec.outcome_metrics:
        return 0

    spus = _target_spus(args)
    if not spus:
        return 0

    applied = applied_on or date.today()
    start = applied - timedelta(days=BASELINE_DAYS)
    end = applied - timedelta(days=1)              # 前窗口不含干预当天
    observe_end = applied + timedelta(days=OBSERVE_DAYS)

    rows = 0
    with get_session() as s:
        # 幂等：同一个提案 + 同一个商品只登记一次
        # （重复执行/重放不应该产生重复的观察记录）
        existing = {
            r.treat_canonical_spu_id for r in s.scalars(
                select(ActionOutcome)
                .where(ActionOutcome.proposal_id == UUID(str(proposal_id)))
            ).all()
        }
        for spu in spus:
            if spu in existing:
                continue
            s.add(ActionOutcome(
                proposal_id=UUID(str(proposal_id)),
                tenant_id=tenant_id,
                treat_canonical_spu_id=spu,
                control_spu_ids=[],          # 对照组在 retro 时才算
                                             # （取决于"之后还有谁被干预"）
                baseline_start=start,
                baseline_end=end,
                observe_end=observe_end,
                status="WAITING",
            ))
            rows += 1
        s.commit()
    return rows


def pending_count() -> int:
    """还在等观察窗口的动作数（给 hub status 用）。"""
    from sqlalchemy import func
    with get_session() as s:
        return s.scalar(select(func.count()).select_from(ActionOutcome)
                        .where(ActionOutcome.status == "WAITING")) or 0
