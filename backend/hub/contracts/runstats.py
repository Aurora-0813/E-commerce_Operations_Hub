# -*- coding: utf-8 -*-
"""
运行统计的契约（runstats.py）—— 阶段 2.6
========================================
`analysis_run.stats` 是 JSONB（自由结构），这带来一个具体问题：

    `hub/observability/health.py::_latest_stats(s, module)` 的查询是
        `AnalysisRun.stats["module"].astext == module`
    → **没写 `module` 的那条记录，永远查不到** ✅

实测（2026-10-31）：6 个写入点里 **只有 `pipeline/runner.py` 漏了 `module`**
（写的是 `{"steps": 12}`），于是"跑一次全链"的统计**从来没被 health 读到过**。

修法分两层：
    ① 本节约：定义 `module` **必填** 的结构（本文件）
    ② 强制校验：`repository.update_status()` 在写入前检查
       → 漏了 `module` 直接报错，**不让它悄悄写进去** ✅

为什么不在数据库层加约束：
    JSONB 里做"必填字段"的 CHECK 约束写起来很绕，而且报错信息对开发者不友好。
    在唯一写入入口（`update_status`）拦一次就够了 —— 所有 6 个写入点都走它 ✅

关于"形状不统一"的澄清：
    各模块的 stats 除了 `module`，其余字段**本来就该不一样**
    （M2 有 `images`、M3 有 `classified`、M4 有 `clusters`）——
    那不是缺陷 ✅。缺陷只有一个：**缺 `module` 导致不可被查询**。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# 已知的 module 取值（不是封闭枚举 —— 加新模块不需要改这里，
# 但如果拼错了，下面的校验会给出提示 ✅）
KNOWN_MODULES = {
    "M2", "M3", "M4-cluster", "M4-price", "M5", "PIPELINE", "EVAL",
}


class RunStats(BaseModel):
    """一次运行要落库的统计。**`module` 必填**。

    用法：
        stats = RunStats(module="M3", counts={"classified": 973},
                         metrics={"grounded_rate": 0.875})
        run_repo.update_status(trace_id, "DONE", stats=stats.to_jsonb())
    """

    module: str = Field(..., min_length=1, description="模块标识，如 M2/M3/M5/PIPELINE")
    counts: dict = Field(default_factory=dict, description="计数类（条数/失败数）")
    metrics: dict = Field(default_factory=dict, description="比率/耗时/token 等")
    detail: dict = Field(default_factory=dict, description="其它任意补充信息")

    @field_validator("module")
    @classmethod
    def _known(cls, v: str) -> str:
        # 不阻止新模块，但拼错时给一条明确提示（比"查不到数据"好排查得多）
        if v not in KNOWN_MODULES:
            raise ValueError(
                f"未知 module {v!r}。已知：{sorted(KNOWN_MODULES)}。"
                "如果是新模块，请同时把它加进 runstats.KNOWN_MODULES "
                "和 docs/实现状态.md。"
            )
        return v

    def to_jsonb(self) -> dict:
        """落库用的平铺 dict。

        **保持平铺**（不是 `{"module":..,"counts":{..}}` 的分层）——
        因为 `_latest_stats` 和现有读取方都是平铺读 `stats["xxx"]`，
        改成嵌套会一次性打破所有读取方 ✅（重构的代价要控制住）
        """
        out: dict = {"module": self.module}
        out.update(self.counts)
        out.update(self.metrics)
        out.update(self.detail)
        return out


def check_stats(stats: dict | None) -> dict | None:
    """写入前的守门：`stats` 必须是 dict，且**必须带 `module`**。

    :raises ValueError: 缺 module（这是导致"记录查不到"的唯一原因）
    """
    if stats is None:
        return None
    if not isinstance(stats, dict):
        raise ValueError(f"stats 必须是 dict，收到 {type(stats).__name__}")
    if not stats.get("module"):
        raise ValueError(
            "stats 缺少必填字段 `module` —— 这样的记录 `_latest_stats()` 永远查不到"
            f"（hub/observability/health.py）。收到：{stats}。"
            "请用 hub/contracts/runstats.py::RunStats 构造，或手动加 module。"
        )
    return stats
