# -*- coding: utf-8 -*-
"""
全链验证（run_all.py）
=====================
一条命令从"原始导出文件"跑到"归因报告"，验证整条链路可复现。

    ① 清空数据库（连外键级联一起验证）
    ② 导入 6 个导出文件（淘宝/京东 × 评价/商品/销售）
    ③ 回填 image_urls（M2 靠它找图片文件）
    ④ 准备图片包（目录 + zip 同步重建）
    ⑤ 训练 DQE 模型（读 gold_labels）
    ⑥ 跑 DQE 降噪
    ⑦ 跑 M2 多模态（图片，约 3 分钟）
    ⑧ 跑 M3 归因链（分类 + 归因，约 4 分钟）
    ⑨ 同款对齐（M11，写入 sku_mapping）
    ⑩ 问题簇聚类（M4，约 35 秒）
    ⑪ 价格背离检测（M4）
    ⑫ 优先级打分（M4）
    ⑬ 生成提案 + 过网关（M5；L1 自动执行、L2/L3 转人工待办）

为什么需要这个脚本：
    之前一直是**分段验证**（导入、DQE、M2、M3 分开跑），
    没验证过"清空数据库后一条命令跑到底"。
    这能证明两件事：
        1. **可复现**：别人 clone 下来能跑出一样的结果
        2. **无隐藏依赖**：不会出现"分段跑没事、连起来跑就炸"

运行（在 backend/ 目录下，约 8~10 分钟）：
    python scripts/run_all.py
    python scripts/run_all.py --no-reset    # 不清库，只在现有数据上跑
"""
import argparse
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.cli import (                                    # noqa: E402
    cmd_align,
    cmd_cluster,
    cmd_dqe,
    cmd_mm,
    cmd_p0,
    cmd_price,
    cmd_priority,
    cmd_propose,
    cmd_topic,
)
from scripts import (                                    # noqa: E402
    backfill_image_url,
    prepare_image_package,
    reset_data,
    train_dqe,
)

DATA = _BACKEND_DIR.parent / "data" / "mock"
PACKAGE = DATA / "images"

# 6 个导出文件：(文件名, 平台, 类型)
IMPORTS = [
    ("taobao_review_202610.xlsx", "taobao", "review"),
    ("jd_review_202610.xlsx", "jd", "review"),
    ("taobao_product_202610.xlsx", "taobao", "product"),
    ("jd_product_202610.xlsx", "jd", "product"),
    ("taobao_sales_202610.xlsx", "taobao", "sales"),
    ("jd_sales_202610.xlsx", "jd", "sales"),
]


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 64}\n[{n}] {title}\n{'=' * 64}")


def summary() -> None:
    """跑完打印一张数据快照，确认每一步都落库了。"""
    from sqlalchemy import func, select

    from hub.persistence.db import get_session
    from hub.persistence.models import (
        ActionProposal,
        AnalysisRun,
        Anomaly,
        HitlTask,
        IngestBatch,
        IssueCluster,
        Record as RecordRow,
        ReviewFeature,
        ReviewReplyDraft,
        ReviewTopic,
        SkuMapping,
        ToolExecution,
    )

    with get_session() as s:
        items = [
            ("ingest_batch", s.scalar(select(func.count()).select_from(IngestBatch))),
            ("record", s.scalar(select(func.count()).select_from(RecordRow))),
            ("record(keep)",
             s.scalar(select(func.count()).select_from(RecordRow)
                      .where(RecordRow.dqe_decision == "keep"))),
            ("review_feature", s.scalar(select(func.count()).select_from(ReviewFeature))),
            ("review_topic", s.scalar(select(func.count()).select_from(ReviewTopic))),
            ("sku_mapping", s.scalar(select(func.count()).select_from(SkuMapping))),
            ("issue_cluster", s.scalar(select(func.count()).select_from(IssueCluster))),
            ("anomaly(price)",
             s.scalar(select(func.count()).select_from(Anomaly)
                      .where(Anomaly.type == "price"))),
            ("action_proposal", s.scalar(select(func.count()).select_from(ActionProposal))),
            ("tool_execution", s.scalar(select(func.count()).select_from(ToolExecution))),
            ("hitl_task(待批)", s.scalar(select(func.count()).select_from(HitlTask)
                                        .where(HitlTask.status == "PENDING"))),
            ("reply_draft", s.scalar(select(func.count()).select_from(ReviewReplyDraft))),
            ("analysis_run", s.scalar(select(func.count()).select_from(AnalysisRun))),
        ]
    print("\n最终数据快照：")
    for name, n in items:
        print(f"  {name:16} {n}")


def _pyflakes_undefined() -> list[str] | None:
    """用 pyflakes 查"未定义的名字"。返回问题列表；没装 pyflakes 返回 None。

    **只让"未定义名字"挡路**，其他（未使用的 import、f-string 没占位符）
    只当提示 —— 那些是风格问题，不该阻塞整条全链。
    """
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pyflakes",
             str(_BACKEND_DIR / "hub"), str(_BACKEND_DIR / "scripts")],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if r.returncode < 0:            # 进程被杀之类的，不当作检查结果
        return None
    return [ln for ln in (r.stdout or "").splitlines()
            if "undefined name" in ln]


def main() -> None:
    ap = argparse.ArgumentParser(description="全链验证：原始导出 → 归因报告")
    ap.add_argument("--no-reset", action="store_true", help="不清空数据库")
    args = ap.parse_args()

    # 整个全链共用一个 trace_id：M1~M5 的 span 都会挂到它下面，最后打印成一棵树
    from hub.core.tracing import print_tree, set_trace_id
    trace_id = set_trace_id()
    print(f"[trace_id] {trace_id}\n")

    t0 = time.time()
    n = 0

    # 第 0 步：代码自检（语法 + 未定义名字）
    #
    # 为什么要放在【清库之前】：
    #   只要有一处错，全链会先清库、再崩 —— 库被清了，要的东西没跑出来。
    #   实测踩过：编辑时删掉行尾换行导致两行粘连（3 次），
    #             以及 `select` / `claim_idle` 两个未定义名字（pyflakes 抓出来的）。
    #
    # **为什么光有 compileall 不够**：
    #   `select(TraceSpan...)` 语法完全合法，只是那个名字没 import ——
    #   compileall 查不出来，要跑到那一行才 NameError。
    #   而这两处错误都藏在"正常路径不会走"的分支里（hub trace 不带 id、
    #   崩溃恢复的续租），所以一直没炸，测试还假绿。
    #   → 必须加 pyflakes 查"未定义的名字"。
    n += 1
    step(n, "代码自检（语法 + 未定义名字）")
    import compileall
    ok = all([
        compileall.compile_dir(str(_BACKEND_DIR / "hub"), quiet=2),
        compileall.compile_dir(str(_BACKEND_DIR / "scripts"), quiet=2),
    ])
    if not ok:
        raise SystemExit("❌ 语法错误，已中止（数据库未被清空）")

    undefined = _pyflakes_undefined()
    if undefined is None:
        print("  ⚠️ 没装 pyflakes，跳过了\"未定义名字\"检查"
              "（pip install pyflakes 可以打开）")
    elif undefined:
        print("  ❌ 发现未定义的名字（跑到那行必崩）：")
        for line in undefined:
            print(f"     {line}")
        raise SystemExit("❌ 未定义名字，已中止（数据库未被清空）")
    else:
        print("  语法通过｜无未定义名字")

    if not args.no_reset:
        n += 1
        step(n, "清空数据库（验证外键级联）")
        reset_data.main()

    n += 1
    step(n, "导入导出文件（6 个）")
    for filename, platform, dtype in IMPORTS:
        path = DATA / filename
        if not path.exists():
            print(f"❌ 文件不存在：{path}")
            return
        cmd_p0(str(path), platform, dtype, save=True)

    n += 1
    step(n, "回填 image_urls")
    backfill_image_url.main()

    n += 1
    step(n, "准备图片包（目录 + zip）")
    prepare_image_package.main()

    n += 1
    step(n, "训练 DQE 模型")
    train_dqe.main()

    n += 1
    step(n, "跑 DQE 降噪")
    cmd_dqe(force=True)

    n += 1
    step(n, "跑 M2 多模态（图片）")
    cmd_mm(str(PACKAGE), force=True, vision="ollama")

    n += 1
    step(n, "跑 M3 归因链（分类 + 归因）")
    cmd_topic(force=True)

    # ---- M11 + M4：同款对齐 → 聚类 → 价格背离 → 优先级 ----
    # 顺序不能乱：先对齐（sku_mapping），价格背离才有跨平台数据可比；
    # 先聚类，优先级才有簇可打分。
    n += 1
    step(n, "M11 同款对齐（sku_mapping）")
    cmd_align()

    n += 1
    step(n, "M4 问题簇聚类（topic → 子簇）")
    cmd_cluster()

    n += 1
    step(n, "M4 价格背离检测（anomaly）")
    cmd_price()

    n += 1
    step(n, "M4 优先级打分（P0~P3）")
    cmd_priority()

    # ---- M5：从 P0/P1 簇生成提案，过网关（L0/L1 自动执行，L2/L3 转待办）----
    n += 1
    step(n, "M5 生成提案 + 过网关（L1 自动执行 / L2L3 转人工）")
    cmd_propose()

    summary()

    print(f"\n{'=' * 64}")
    print(f"✅ 全链完成，用时 {time.time() - t0:.1f}s")
    print(f"{'=' * 64}")

    # 最后打印这次运行的 span 树（哪一步慢、哪次 LLM 调用贵，一目了然）
    print(f"\n{'=' * 64}")
    print("本次运行的 trace（也可以用 `python -m hub.cli trace "
          f"{trace_id}` 再查）")
    print(f"{'=' * 64}")
    print_tree(trace_id)

    # 自动刷新数据快照（docs/实测数据.md）
    #
    # 为什么放在这里：`docs/工程规范.md` §6 要求"精确数字只有一个来源"。
    #   如果快照要人工记得跑，那它迟早会过期——**"记得跑"不是机制**。
    #   接在全链末尾，就等于"每次数据变了，快照自动跟着变"。
    n += 1
    step(n, "刷新数据快照（docs/实测数据.md）")
    try:
        import snapshot
        snapshot.main()
    except Exception as e:                        # noqa: BLE001
        # 快照失败不该让"已经跑成功的全链"看起来像失败
        print(f"  ⚠️ 快照生成失败（不影响全链结果）：{e}")


if __name__ == "__main__":
    main()
