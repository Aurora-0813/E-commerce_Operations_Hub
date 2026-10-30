# -*- coding: utf-8 -*-
"""
链路追踪（tracing.py）
=====================
一次分析要跑十几步、几十次 LLM 调用。出了问题（第 5 步慢、第 7 步抛异常、
某个批次耗时异常），没有 trace 就只能瞎猜。

**这一版解决三件事**：
    ① **嵌套**：用 contextvar 自动传播父子关系，不用手动传 parent_id
    ② **记 token**：LLM 客户端把 Ollama 返回的 prompt_eval_count / eval_count 写进 span
    ③ **时区**：统一用 UTC aware 时间（原来用 naive datetime，和 DateTime(timezone=True) 不匹配）

用法：
    with run_trace() as trace_id:          # 一次运行一个 trace
        with span("m3.analysis"):           # 顶层 span
            with span("m3.classify"):       # 自动成为上一条的子 span
                llm.chat(...)               # LLM 客户端自己开 "llm.chat" 子 span 并记 token
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from hub.persistence.db import get_session
from hub.persistence.models import TraceSpan

# 当前 trace（一次运行一个）
_current_trace: ContextVar[str | None] = ContextVar("current_trace", default=None)
# 当前 span（用于自动建立父子关系）
_current_span: ContextVar["SpanCtx | None"] = ContextVar("current_span", default=None)


def now() -> datetime:
    """统一的 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


# ==================== span 上下文 ====================

@dataclass
class SpanCtx:
    """一个正在进行的 span。

    业务代码可以往它上面挂东西：
        with span("m3.classify") as sp:
            sp.add(pending=450, engine="batch")
            sp.add_tokens(1024, 312)
    """
    trace_id: str
    span_id: str
    name: str
    kind: str = "step"
    attrs: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0

    def add(self, **kw) -> None:
        """挂自定义属性（进 JSONB 的 attrs）。"""
        self.attrs.update(kw)

    def add_tokens(self, tokens_in: int | None, tokens_out: int | None) -> None:
        """累加 token（同一次调用可能分多批，所以是加法不是赋值）。"""
        if tokens_in:
            self.tokens_in += int(tokens_in)
        if tokens_out:
            self.tokens_out += int(tokens_out)


def current_trace_id() -> str | None:
    return _current_trace.get()


def current_span() -> SpanCtx | None:
    """当前 span（LLM/工具客户端用它挂 token / 属性）。没有就返回 None。"""
    return _current_span.get()


def set_trace_id(trace_id: str | None = None) -> str:
    """直接设置当前 trace（不自动复位）。

    给 **CLI 进程**用：进程跑完就退出，不需要复位。
    需要嵌套/复位的场景（比如一个进程跑多次）请用 `run_trace()`。
    """
    tid = trace_id or uuid4().hex
    _current_trace.set(tid)
    return tid


@contextmanager
def run_trace(trace_id: str | None = None):
    """开一次运行。里面所有 span 共享这个 trace_id。

    :return: 实际使用的 trace_id（不传就生成一个）
    """
    tid = trace_id or uuid4().hex
    token = _current_trace.set(tid)
    try:
        yield tid
    finally:
        _current_trace.reset(token)


# ==================== 核心：span ====================

@contextmanager
def span(name: str, trace_id: str | None = None, parent_id: str | None = None,
         *, kind: str = "step", **attrs):
    """记录一个 span（上下文管理器）。

    :param name:      span 名称，如 "m3.classify" / "llm.chat" / "tool.update_price"
    :param trace_id:  所属运行；不传就取当前上下文（`run_trace` 设的）
    :param parent_id: 父 span；不传就自动取"当前正在进行的 span"
    :param kind:      step / llm / tool / vision
    :param attrs:     自定义属性，原样进 JSONB

    嵌套是**自动**的：只要在 `with span(...)` 里面再开一个，就自动是子 span。
    """
    tid = trace_id or _current_trace.get() or "no-trace"
    parent = parent_id if parent_id is not None else (
        _current_span.get().span_id if _current_span.get() else None
    )
    ctx = SpanCtx(trace_id=tid, span_id=uuid4().hex, name=name, kind=kind,
                  attrs=dict(attrs))
    start = now()
    status = "OK"

    token = _current_span.set(ctx)
    try:
        yield ctx
    except Exception as e:                      # noqa: BLE001
        status = "ERROR"
        ctx.attrs.setdefault("error", f"{type(e).__name__}: {e}"[:300])
        raise
    finally:
        _current_span.reset(token)
        end = now()
        ctx.attrs.setdefault("duration_ms", int((end - start).total_seconds() * 1000))
        _write_span(ctx, parent, status, start, end)


def _write_span(ctx: SpanCtx, parent_id: str | None, status: str,
                start: datetime, end: datetime) -> None:
    """落库。写失败**绝不影响业务**——trace 是观测，不是主流程。

    ⚠️ 但**失败要出声**：静默吞掉的话，你会以为"有 trace"，其实一条没写进去。
    这里打到 stderr（不污染正常输出），方便排查。
    """
    try:
        with get_session() as s:
            s.add(TraceSpan(
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                parent_id=parent_id,
                name=ctx.name,
                kind=ctx.kind,
                status=status,
                start_ts=start,
                end_ts=end,
                tokens_in=ctx.tokens_in or None,
                tokens_out=ctx.tokens_out or None,
                attrs=ctx.attrs,
            ))
            s.commit()
    except Exception as e:                      # noqa: BLE001
        import sys
        print(f"[trace] 写 span 失败（{ctx.name}）：{type(e).__name__}: {e}",
              file=sys.stderr)


# ==================== 装饰器：给模块入口加 span ====================

def traced(name: str, kind: str = "step"):
    """给一个函数套上 span（一行就能埋点）。

        @traced("m4.cluster")
        def run_clustering(...):
            ...

    为什么用装饰器而不是手动 `with`：
        模块入口函数体动辄上百行，手动包一层要整体缩进，
        diff 会变成"全文件重写"，review 时看不出真实改动。
    """
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(name, kind=kind):
                return fn(*args, **kwargs)
        return wrapper
    return deco


# ==================== 读取：把平铺的 span 拼成树 ====================

def trace_tree(trace_id: str) -> list[dict]:
    """取一次运行的所有 span，按父子关系排成"树序"（深度优先）。

    返回的每一项带 `depth`，CLI 按 depth 缩进打印。

    **相同名字的兄弟会折叠**：M3 有 45 次 `llm.chat`，全展开会刷屏，
    所以连续同名兄弟合并成一条 `llm.chat ×45`（耗时和 token 求和）。
    """
    from sqlalchemy import select

    with get_session() as s:
        rows = s.scalars(
            select(TraceSpan).where(TraceSpan.trace_id == trace_id)
            .order_by(TraceSpan.start_ts)
        ).all()
        spans = [
            {
                "span_id": r.span_id,
                "parent_id": r.parent_id,
                "name": r.name,
                "kind": r.kind,
                "status": r.status,
                "start_ts": r.start_ts,
                "end_ts": r.end_ts,
                "tokens_in": r.tokens_in or 0,
                "tokens_out": r.tokens_out or 0,
                "attrs": r.attrs or {},
            }
            for r in rows
        ]

    by_parent: dict[str | None, list[dict]] = {}
    for sp in spans:
        by_parent.setdefault(sp["parent_id"], []).append(sp)

    flat: list[dict] = []

    def walk(parent: str | None, depth: int) -> None:
        children = by_parent.get(parent, [])
        # 折叠连续同名兄弟
        collapsed: list[dict] = []
        for child in children:
            if collapsed and collapsed[-1]["name"] == child["name"] \
                    and collapsed[-1].get("_folded"):
                prev = collapsed[-1]
                prev["_count"] += 1
                prev["tokens_in"] += child["tokens_in"]
                prev["tokens_out"] += child["tokens_out"]
                if child["end_ts"] and prev["end_ts"]:
                    prev["end_ts"] = max(prev["end_ts"], child["end_ts"])
                if prev["start_ts"] and child["start_ts"]:
                    prev["start_ts"] = min(prev["start_ts"], child["start_ts"])
            else:
                node = dict(child)
                node["_count"] = 1
                node["_folded"] = True
                collapsed.append(node)
        for node in collapsed:
            node = dict(node)
            node.pop("_folded", None)
            node["depth"] = depth
            flat.append(node)
            walk(node["span_id"], depth + 1)

    walk(None, 0)
    return flat


def format_tree(trace_id: str) -> str:
    """把一次运行的 span 树格式化成可打印的文本。"""
    rows = trace_tree(trace_id)
    if not rows:
        return f"没有找到 trace={trace_id} 的 span（是不是还没跑过？）"

    total_s = sum((r["end_ts"] - r["start_ts"]).total_seconds()
                  for r in rows
                  if r["end_ts"] and r["start_ts"] and r["depth"] == 0)
    total_in = sum(r["tokens_in"] for r in rows if r["depth"] >= 0)
    total_out = sum(r["tokens_out"] for r in rows if r["depth"] >= 0)

    lines = [f"trace {trace_id}",
             f"  顶层耗时 {total_s:.1f}s｜tokens {total_in} 入 / {total_out} 出", ""]
    for r in rows:
        dur = ((r["end_ts"] - r["start_ts"]).total_seconds()
               if r["end_ts"] and r["start_ts"] else 0.0)
        count = f" ×{r['_count']}" if r.get("_count", 1) > 1 else ""
        tok = f"  tokens {r['tokens_in']}/{r['tokens_out']}" if r["tokens_in"] else ""
        flag = "" if r["status"] == "OK" else f"  ⚠ {r['status']}"
        kind = "" if r["kind"] == "step" else f" [{r['kind']}]"
        lines.append(f"  {'│  ' * r['depth']}├─ {r['name']}{kind}{count}"
                     f"  {dur:8.2f}s{tok}{flag}")
    return "\n".join(lines)


def print_tree(trace_id: str) -> None:
    print(format_tree(trace_id))
