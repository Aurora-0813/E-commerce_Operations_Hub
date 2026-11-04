# -*- coding: utf-8 -*-
"""
阶段内进度上报（progress.py）—— 2026-11-04
=========================================
**为什么需要**
    前端进度条原来**只在阶段边界跳**：

        导入 16% → 降噪 27% → 分类 38% → …

    但 `topic`（分类+归因）要跑 **3~4 分钟** ✅
    → 这 4 分钟里百分比**一动不动** ✅ ✗
    → 用户以为"卡死了"，实际它在**闷头干活**（每批 10 条，2 秒一行输出）

**怎么做**
    · worker 在跑某个阶段前，调 `set_run(run_id)` 告诉这里"现在跑的是谁"
    · 阶段内部（比如分类的分批循环）每批调 `report(done, total)`
    · `report` 把 `{stage, done, total}` 写进 `pipeline_run.params.progress` ✅
      （节流：同一次进度变化小于 2% 就不写库，避免 UPDATE 打爆 ✅）
    · `/api/runs/{id}` 把它返回给前端 ✅
    · 前端：`percent = (阶段序号 + 阶段内百分比) / 总阶段数` ✅

**为什么用全局变量而不是参数**
    阶段函数的签名是 `fn(params: dict)`，加参数要改 8 个函数 ✅
    worker 是**单线程**跑一个阶段 ✅ → 用「当前 run_id」全局完全安全 ✅
    （两个 worker 是两个进程，各自一个全局 ✅）
"""
from __future__ import annotations

from typing import Any

#: 当前正在跑的 run（worker 设置 ✅）
_CURRENT: dict[str, Any] = {"run_id": None, "stage": None, "done": 0, "total": 0,
                            "last_pct": -1}


def set_run(run_id: str | None, stage: str | None = None) -> None:
    """worker 在**开始跑一个阶段前**调这个 ✅"""
    _CURRENT.update(run_id=run_id, stage=stage, done=0, total=0, last_pct=-1)


def current() -> dict[str, Any]:
    return dict(_CURRENT)


def report(done: int, total: int, *, stage: str | None = None) -> None:
    """阶段内部报进度（比如分类每写完一批 ✅）

    :param done:  已完成数
    :param total: 总数
    :param stage: 阶段名（不传就用 `set_run` 时的 ✅）

    节流：进度变化 < 2% 直接返回（不写库 ✅）
    """
    if total <= 0:
        return
    pct = int(done * 100 / total)
    if pct - _CURRENT["last_pct"] < 2 and done < total:
        return

    run_id = _CURRENT.get("run_id")
    st = stage or _CURRENT.get("stage")
    _CURRENT.update(done=done, total=total, last_pct=pct, stage=st)
    if not run_id:
        return

    # 写库：`params || {"progress": {...}}`（JSONB 浅合并 ✅）
    try:
        from sqlalchemy import text

        from hub.persistence.db import get_session

        with get_session() as s:
            s.execute(
                text("""
                    UPDATE pipeline_run
                       SET heartbeat_at = now(),
                           params = COALESCE(params, '{}'::jsonb)
                                    || jsonb_build_object(
                                         'progress',
                                         jsonb_build_object(
                                           'stage', CAST(:stage AS text),
                                           'done',  CAST(:done  AS int),
                                           'total', CAST(:total AS int),
                                           'pct',   CAST(:pct   AS int)))
                     WHERE id = CAST(:rid AS uuid)
                """),
                {"stage": st, "done": done, "total": total, "pct": pct,
                 "rid": run_id},
            )
            s.commit()
    except Exception as e:                      # noqa: BLE001
        # **进度上报失败绝不影响业务** ✅（只是界面不好看 ✅）
        print(f"    [progress] 写库失败（忽略）：{type(e).__name__}: {e}")


def clear() -> None:
    """worker 跑完一个阶段后清掉 ✅"""
    _CURRENT.update(run_id=None, stage=None, done=0, total=0, last_pct=-1)
