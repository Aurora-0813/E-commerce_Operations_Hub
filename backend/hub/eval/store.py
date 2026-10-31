# -*- coding: utf-8 -*-
"""
评测结果落库（store.py）—— 阶段 2.1
====================================
**为什么要有这个**：`hub eval` 之前只 print，跑完就没了 ✅
   ↓
于是回答不了这些真实问题：
   · 过去 30 天，门禁通过率是多少？（是不是悄悄退化了）
   · 上次改提示词之前，硬门禁是哪一条开始红的？
   · 换模型之后，软门禁掉了几个点？
   ↓
**没有历史的"质量门禁"，等于只有一次性体检** ✅

两张表的分工（列名见 `persistence/models.py`）：
    eval_case   用例定义（YAML 是权威，这里只是同步一份，便于 JOIN 统计）
                id / category / gate_type / fixture_path / assertion / enabled
    eval_result 每次运行、每条用例一行（append-only）
                id / run_id / case_id / passed / detail / created_at

设计取舍：
    · `run_id` 用「时间戳」而不是 UUID —— 因为人要看"哪天跑的"，可读性优先
    · `fixture_path` 填 YAML 文件路径（用例没有独立 fixture 文件，来源就是 YAML）
    · `detail` 存 expect + actual + error，**失败时才有排查价值**
    · 用例同步用「先查后写」而不是 ON CONFLICT —— 只有 20 条，可读性优先
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select

from hub.eval.runner import CaseResult, EvalReport
from hub.persistence.db import get_session
from hub.persistence.models import EvalCase, EvalResult

# 用例来源文件（相对 backend/），写进 eval_case.fixture_path
SUITE_PATH = "hub/eval/suites/{suite}.yaml"


def make_run_id() -> str:
    """运行 ID：`eval-20261031-085512-a3f1`（日期-时分秒 + 4 位随机后缀）。

    ⚠️ **必须能区分"同一分钟内跑两次"** —— 这里踩过一次：
        v1 只精确到分钟（`eval-20261031-0855`），结果连续跑两次时
        **两次的结果被合并进同一个 run_id** ✅
        于是 history 显示 `38/38`（19+19）和 `2/2`（1+1），
        **看起来像一个健康的记录，实际是两次运行被拍扁了一次** ✅ ✗
    → 时间到秒 + 随机后缀：既"人能看懂哪天跑的"，又保证唯一 ✅
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"eval-{stamp}-{uuid.uuid4().hex[:4]}"


def sync_cases(suite_name: str, cases: list[dict]) -> int:
    """把 YAML 里的用例同步进 `eval_case`（新增 + 更新，不删）。

    YAML 是唯一权威来源 ✅ —— 这张表只是镜像，用于 JOIN 统计。

    :return: 同步了多少条
    """
    path = SUITE_PATH.format(suite=suite_name)
    with get_session() as s:
        existing = {c.id: c for c in s.scalars(select(EvalCase)).all()}
        n = 0
        for raw in cases:
            cid = raw["id"]
            fields = dict(
                category=raw.get("category", ""),
                gate_type=raw.get("gate", "hard"),
                fixture_path=path,
                assertion=raw.get("expect") or {},
                enabled=True,
            )
            row = existing.get(cid)
            if row is None:
                s.add(EvalCase(id=cid, **fields))
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
            n += 1
        s.commit()          # ← 必须显式提交：get_session() 不会替你 commit
    return n


def save_report(report: EvalReport, suite_name: str) -> str:
    """保存一次评测结果，返回 run_id。

    调用前必须已经 `sync_cases`（否则外键会失败 ✅ —— 这是刻意的：
    **宁可报错，也不要写出一条指向不存在用例的结果**）。
    """
    run_id = make_run_id()
    with get_session() as s:
        for r in report.results:
            s.add(EvalResult(
                run_id=run_id,
                case_id=r.id,
                passed=bool(r.passed),
                # 失败时才需要排查，所以 detail 里把三方都留着
                detail={
                    "gate": r.gate,
                    "category": r.category,
                    "expect": r.expect,
                    "actual": _jsonable(r.actual),
                    "error": r.error,
                },
            ))
        s.commit()          # ← 必须显式提交（踩过：不提交时不报错，但历史里永远是空的）
    return run_id


def _jsonable(v):
    """把 actual 里可能出现的非 JSON 类型转成可存的形式。

    为什么需要：actual 来自工具/模型返回值，可能含 Decimal / UUID / datetime。
    直接塞进 JSONB 会报 `TypeError: Object of type Decimal is not JSON serializable` ✅
    """
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def history(limit: int = 10) -> list[dict]:
    """最近 N 次运行的汇总（按 run_id 分组）。

    :return: [{"run_id": ..., "at": ..., "hard": (pass, total), "soft": (pass, total)}]
    """
    with get_session() as s:
        rows = s.execute(
            select(EvalResult.run_id, EvalResult.passed, EvalResult.detail)
        ).all()

    # 在内存里聚合（只有几百行，不值得写复杂 SQL）
    agg: dict[str, dict] = {}
    for run_id, passed, detail in rows:
        a = agg.setdefault(run_id, {"hard": [0, 0], "soft": [0, 0], "at": None})
        gate = (detail or {}).get("gate", "hard")
        bucket = a["hard"] if gate == "hard" else a["soft"]
        bucket[1] += 1
        if passed:
            bucket[0] += 1

    out = [dict(run_id=k, **v) for k, v in agg.items()]
    # run_id 形如 eval-20261031-0845，字符串排序就是时间排序
    out.sort(key=lambda x: x["run_id"], reverse=True)
    return out[:limit]


def failures(run_id: str) -> list[CaseResult]:
    """某次运行里失败的用例（用于 `--history` 之后追问"哪条红了"）。"""
    with get_session() as s:
        rows = s.scalars(select(EvalResult).where(
            EvalResult.run_id == run_id, EvalResult.passed.is_(False))).all()
        return [
            CaseResult(
                id=r.case_id,
                category=(r.detail or {}).get("category", ""),
                gate=(r.detail or {}).get("gate", "hard"),
                title="",
                passed=False,
                expect=(r.detail or {}).get("expect") or {},
            )
            for r in rows
        ]


def clear(keep: int = 200) -> int:
    """清理旧结果，只保留最近 `keep` 次运行（防止 eval_result 无限增长）。

    和 `queue/commands.py::_prune_old_runs` 是同一个思路 ✅
    —— 每次全量评测写 20 行，一天跑 3 次就是 60 行/天。
    """
    runs = [h["run_id"] for h in history(limit=10_000)]
    if len(runs) <= keep:
        return 0
    drop = runs[keep:]
    with get_session() as s:
        deleted = s.execute(
            delete(EvalResult).where(EvalResult.run_id.in_(drop))
        ).rowcount
        s.commit()          # ← 同上，必须显式提交
    return int(deleted or 0)
