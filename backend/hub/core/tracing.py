# -*- coding: utf-8 -*-
"""
链路追踪（tracing.py）
=====================
一个极简的 span 记录器：在每步前后打点，把开始/结束时间、状态写入 trace_span 表。

为什么要有它：
    一次分析跑了 8 步，出了问题（比如第 5 步慢、第 7 步抛异常），
    没有 trace 就只能瞎猜。有了 trace，能精确回答"哪一步、花了多久、成功没有"。

用法：
    with span("dqe", trace_id, impl="rule.v1"):
        ...  # 这一步的业务代码
"""
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from hub.persistence.db import get_session
from hub.persistence.models import TraceSpan


@contextmanager
def span(name: str, trace_id: str, parent_id: str | None = None, **attrs):
    """记录一个 span（上下文管理器）。

    :param name:      span 名称，如 "ingest" / "dqe"
    :param trace_id:  所属运行的 ID（贯穿全链）
    :param parent_id: 父 span（可空；骨架阶段都是顶层）
    :param attrs:     自定义属性，原样存进 JSONB
    """
    span_id = uuid4().hex          # span 唯一 ID
    start = datetime.now()         # 开始时间
    status = "OK"                  # 默认成功

    try:
        yield                      # 把控制权交给 with 块里的业务代码
    except Exception:
        status = "ERROR"           # 业务抛异常 → 标记失败
        raise                      # 异常继续往上抛，不吞掉
    finally:
        # 无论成功失败，都把这条 span 落库
        end = datetime.now()
        with get_session() as s:
            s.add(TraceSpan(
                trace_id=trace_id,
                span_id=span_id,
                parent_id=parent_id,
                name=name,
                kind="step",           # 类型：步骤 / llm / tool
                status=status,         # OK / ERROR
                start_ts=start,
                end_ts=end,
                attrs=attrs,           # 自定义属性
            ))
            s.commit()
