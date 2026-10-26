# -*- coding: utf-8 -*-
"""
验证存储层：建批次 → 建运行 → 查询 → 更新状态 → 再查询。

前提：表已经建好（先跑 alembic upgrade head）。

注意：每次运行都生成**唯一**的 file_sha256 和 trace_id，
否则第二次跑会撞唯一约束（这恰好说明 uq_batch_hash 在工作）。

运行（在 backend/ 目录下）：
    python tests/check_db.py
"""
import sys
from pathlib import Path
from uuid import uuid4

# 把 backend/ 加入 sys.path：这样从任何目录（终端/IDE）运行，都能 import hub
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.persistence.db import get_session          # noqa: E402
from hub.persistence.models import IngestBatch       # noqa: E402
from hub.persistence.repository import RunRepo       # noqa: E402


def main() -> None:
    run_tag = uuid4().hex[:8]          # 本次测试的唯一后缀
    trace_id = f"trace_test_{run_tag}"

    # 1. 先建一个导入批次（analysis_run.batch_id 外键依赖它）
    print("=== 1. 建导入批次 ===")
    batch_id = uuid4()
    with get_session() as s:
        s.add(IngestBatch(
            id=batch_id,
            tenant_id="default",
            platform="jd",
            file_name="jd_review_202610.xlsx",
            file_sha256=uuid4().hex * 2,   # 每次不同，避免撞 uq_batch_hash
        ))
        s.commit()
    print(f"✅ 批次创建成功：batch_id={batch_id}")

    # 2. 建一次运行（引用上面的批次）
    print("\n=== 2. 建分析运行 ===")
    repo = RunRepo()
    repo.create(trace_id, batch_id)
    run = repo.get(trace_id)
    print(f"✅ 运行创建成功：trace_id={run.trace_id} status={run.status}")

    # 3. 更新状态
    print("\n=== 3. 更新状态为 DONE ===")
    repo.update_status(trace_id, "DONE", stats={"steps": 7, "tokens": 12000})
    run = repo.get(trace_id)
    print(f"✅ 更新成功：status={run.status} stats={run.stats} 结束时间={run.finished_at}")

    print("\n存储层验证结束。把输出贴给我。")


if __name__ == "__main__":
    main()
