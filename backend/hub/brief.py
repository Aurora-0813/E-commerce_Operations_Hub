# -*- coding: utf-8 -*-
"""
AI 日报（brief.py）—— 阶段 3.6
=============================
路线图 §九：

    输入：`status`（13 项）+ `health`（7 项）+ 今日变化（vs 昨天）
    输出：3~5 条人话
    **校验：草稿里每个数字，必须能在输入里找到**
       · 允许单位后缀（条/个/%/万/pp/次）
       · 找不到 → 丢弃那一条；丢弃超过 1 条 → 重写一次；再不行 → 只输出原始指标表
    存：`analysis_run`（`stats.module="brief"`、`stats.text`、`stats.inputs`）—— 不新建表

**为什么这是"唯一名正言顺的云端场景"**（路线图 §2.4）：
    brief 的输入**全是聚合数字**（GMV、差评数、兜底率…）✅
    → CloudGuard 的 `KIND_AGGREGATE` 放行 ✅
    → 不含任何评价原文、不含图片 ✅
    → **上云不违反三条铁律** ✅

**为什么"数字必须能在输入里找到"**：
    LLM 写日报最容易犯的错不是"写得难听"，是**编一个看起来合理的数字** ✅
    （"差评率下降了 12%"—— 而输入里根本没有 12% ✅）
    → 所以逐条校验，找不到就**丢掉那一句** ✅
    → 这是"反幻觉"在这里的具体落法 ✅
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

MIN_LINES = 3
MAX_LINES = 5

BRIEF_SYSTEM = (
    "你是电商运营的助理。下面是一份**今天的运营数据**（全部是聚合数字，没有原始评价）。\n"
    "请写 3~5 条**人话**的日报要点，给运营负责人看。\n"
    "**硬性规则**：\n"
    "  ① 每一条里出现的**每一个数字，都必须能在输入数据里找到**（可以带单位：条/个/%/万/pp/次）；\n"
    "  ② **不许**自己算新的百分比、不许写『下降了 X%』这类输入里没有的数；\n"
    "  ③ 每条一句，最多 40 字，**前面加一个 emoji**；\n"
    "  ④ 只输出这些条目，每行一条，不要加标题、不要解释。"
)

# 单位后缀（校验数字时忽略它们 ✅）
UNIT_SUFFIX = r"(?:条|个|%|万|千|pp|次|天|小时|分钟|元|件|分|名|个簇)?"


@dataclass
class BriefResult:
    """一次日报生成的结果。"""
    lines: list[str] = field(default_factory=list)        # 校验通过的条目
    dropped: list[str] = field(default_factory=list)      # 被丢弃的（数字找不到 ✅）
    fallback_table: bool = False                          # 是否退化成"只输出原始指标表"
    inputs: dict = field(default_factory=dict)
    raw_draft: str = ""
    attempts: int = 0
    error: str = ""


# ════════════════════════════════════════════════════ 输入
def collect_inputs() -> dict:
    """收集 brief 的输入：status（13 项）+ health（7 项）+ 今日 vs 昨天。"""
    out: dict = {}
    # status：库里现状 ✅
    try:
        from hub.observability.status import collect as status_collect
        out["status"] = status_collect()
    except Exception as e:                        # noqa: BLE001
        out["status_error"] = f"{type(e).__name__}: {e}"
    # health：最近一次运行 ✅
    try:
        from hub.observability.health import collect as health_collect
        out["health"] = health_collect(None)
    except Exception as e:                        # noqa: BLE001
        out["health_error"] = f"{type(e).__name__}: {e}"
    # 今日变化（vs 昨天）✅
    try:
        from datetime import date, timedelta

        from sqlalchemy import func, select

        from hub.persistence.db import get_session
        from hub.persistence.models import Record

        today = date.today()
        yday = today - timedelta(days=1)
        with get_session() as s:
            def _cnt(d):
                return int(s.scalar(
                    select(func.count()).select_from(Record)
                    .where(func.date(Record.review_time) == d)) or 0)
            out["change"] = {"今天评价数": _cnt(today), "昨天评价数": _cnt(yday)}
    except Exception as e:                        # noqa: BLE001
        out["change_error"] = f"{type(e).__name__}: {e}"
    return out


def _flatten_numbers(obj, acc: set[str], depth: int = 0) -> None:
    """把输入里**所有数字**收集成一个集合（用于校验 ✅）。"""
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_numbers(v, acc, depth + 1)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _flatten_numbers(v, acc, depth + 1)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        f = float(obj)
        # 同时收：原值、保留 1/2 位、以及 ×100（百分比）✅
        acc.add(f"{f:.0f}")
        acc.add(f"{f:.1f}")
        acc.add(f"{f:.2f}")
        acc.add(f"{f * 100:.0f}")
        acc.add(f"{f * 100:.1f}")
        acc.add(f"{abs(f):.0f}")
        acc.add(f"{abs(f):.1f}")
        acc.add(f"{abs(f) * 100:.0f}")
        acc.add(f"{abs(f) * 100:.1f}")
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            acc.add(m)


def _number_ok(num: str, allowed: set[str]) -> bool:
    """这个数字能在输入里找到吗（**允许单位后缀** ✅）。"""
    n = num.strip()
    if n in allowed:
        return True
    # 去掉末尾的 0（"12.0" → "12"）✅
    try:
        f = float(n)
        for cand in (f"{f:.0f}", f"{f:.1f}", f"{f:.2f}"):
            if cand in allowed:
                return True
    except ValueError:
        pass
    # 允许"万"级的换算：输入 12345.0 而草稿写 "1.2万" ✅
    try:
        f = float(n)
        for scale in (1e4, 1e8):
            v = f * scale
            for cand in (f"{v:.0f}", f"{v:.1f}", f"{v:.2f}"):
                if cand in allowed:
                    return True
    except ValueError:
        pass
    return False


def verify_line(line: str, allowed: set[str]) -> tuple[bool, list[str]]:
    """校验一条：里面的**每个数字**都要能在输入里找到 ✅

    :return: (是否通过, 找不到的数字列表)
    """
    nums = re.findall(r"\d+(?:\.\d+)?", line or "")
    bad = [n for n in nums if not _number_ok(n, allowed)]
    return (not bad), bad


# ════════════════════════════════════════════════════ 生成
def generate(*, llm=None, inputs: dict | None = None,
             max_attempts: int = 2) -> BriefResult:
    """生成日报。**最多重写一次**（路线图 §九 ✅）。"""
    res = BriefResult(inputs=inputs or collect_inputs())
    allowed: set[str] = set()
    _flatten_numbers(res.inputs, allowed)
    if not allowed:
        res.error = "输入里没有任何数字，没法写日报"
        res.fallback_table = True
        return res

    if llm is None:
        from hub.llm import get_text_llm
        llm = get_text_llm()

    import json as _json
    prompt = ("今天的运营数据（JSON）：\n"
              + _json.dumps(res.inputs, ensure_ascii=False, default=str)[:4000]
              + "\n\n请按规则写 3~5 条日报。")
    for attempt in range(1, max_attempts + 1):
        res.attempts = attempt
        try:
            raw = llm.chat(prompt, system=BRIEF_SYSTEM, temperature=0.0, max_tokens=400)
        except Exception as e:                    # noqa: BLE001
            res.error = f"{type(e).__name__}: {e}"
            res.fallback_table = True
            return res
        res.raw_draft = raw
        kept, dropped = [], []
        for ln in [x.strip() for x in (raw or "").splitlines() if x.strip()]:
            ok, bad = verify_line(ln, allowed)
            (kept if ok else dropped).append(ln)
            if not ok:
                res.dropped.append(f"{ln}（找不到的数字：{','.join(bad)}）")
        res.lines = kept[:MAX_LINES]
        # 丢弃 > 1 条 → 重写一次 ✅
        if len(dropped) > 1 and attempt < max_attempts:
            res.dropped = []          # 重写时不累积上一次的 ✅
            continue
        break

    if len(res.lines) < MIN_LINES:
        # 再不行 → 只输出原始指标表 ✅（不硬凑 ✅）
        res.fallback_table = True
    return res


# ════════════════════════════════════════════════════ 落库
def save(res: BriefResult) -> str:
    """存进 `analysis_run`（`stats.module="brief"`）—— **不新建表** ✅

    `RunStats` 的真实字段是 `module / counts / metrics / detail`（2026-11-01 核对 ✅）
    → **没有 `text` 这个字段** ✅ → 日报文本放 `detail["text"]` ✅
    """
    from hub.contracts.runstats import RunStats
    from hub.persistence.db import get_session
    from hub.persistence.models import AnalysisRun

    stats = RunStats(
        module="brief",
        counts={"lines": len(res.lines), "dropped": len(res.dropped),
                "attempts": res.attempts},
        detail={"text": "\n".join(res.lines),
                "inputs": res.inputs,
                "raw_draft": res.raw_draft,
                "fallback_table": res.fallback_table,
                "error": res.error},
    )
    with get_session() as s:
        run = AnalysisRun(trace_id=f"brief-{int(time.time())}-{id(res) % 10000}",
                          batch_id=None, tenant_id="default", status="DONE",
                          stats=stats.to_jsonb())
        s.add(run)
        s.flush()
        rid = str(run.id)
        s.commit()
    return rid


def latest(limit: int = 1) -> list[dict]:
    """最近一次日报（给 `GET /api/brief` 用 ✅）。"""
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import AnalysisRun

    with get_session() as s:
        rows = s.scalars(select(AnalysisRun)
                         .where(AnalysisRun.trace_id.like("brief-%"))
                         .order_by(AnalysisRun.created_at.desc()).limit(limit)).all()
        out = []
        for r in rows:
            st = getattr(r, "stats", None) or {}
            # ⚠️ `RunStats.to_jsonb()` 是**拍平**的（2026-11-01 核对）✅
            #    存进去是 {'module':'brief','text':...,'lines':3,...}
            #    **不是** {'detail': {'text': ...}} ✅ ✗
            #    → 先试 detail，再退回顶层 ✅
            det = (st or {}).get("detail") if isinstance(st, dict) else None
            det = det if isinstance(det, dict) else (st or {})
            out.append({"run_id": str(r.id), "trace_id": r.trace_id,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "text": det.get("text", ""),
                        "counts": {"lines": det.get("lines"),
                                   "dropped": det.get("dropped"),
                                   "attempts": det.get("attempts")},
                        "fallback_table": det.get("fallback_table", False)})
        return out
