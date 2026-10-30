# -*- coding: utf-8 -*-
"""
阶段注册表（stages.py）—— M7 的"工序清单"
=========================================
一条链（`pipeline_run`）要跑哪些阶段、按什么顺序、哪些抢 GPU。

**为什么阶段要注册成数据而不是写死 if/elif**：
    1. `current_stage` 存的是**字符串**，要能从名字反查"下一个是谁"
    2. 断点续跑要能"从第 N 个阶段开始"，必须有有序列表
    3. `hub queue` 要能列出"每个阶段的预估耗时"，用于判断卡住了没

**为什么 `needs_gpu` 要标出来**：
    8GB 显存装不下 7B + 3B 两个模型同时跑。
    多个 worker 时，只有抢到 GPU 锁的那个才能跑 needs_gpu 阶段，
    其余阶段（DQE/对齐/价格/优先级，都是 CPU 秒级）可以并行。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 数据目录（仓库根/data/mock）。从这里算，不依赖当前工作目录
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "mock"

# 默认要导入的 6 个导出文件（和 scripts/run_all.py 一致）
# 格式：(文件名, 平台, 数据类型)
DEFAULT_IMPORTS: list[tuple[str, str, str]] = [
    ("taobao_review_202610.xlsx", "taobao", "review"),
    ("jd_review_202610.xlsx", "jd", "review"),
    ("taobao_product_202610.xlsx", "taobao", "product"),
    ("jd_product_202610.xlsx", "jd", "product"),
    ("taobao_sales_202610.xlsx", "taobao", "sales"),
    ("jd_sales_202610.xlsx", "jd", "sales"),
]

IMAGE_PACKAGE = DATA_DIR / "images"


@dataclass(frozen=True)
class Stage:
    """一个阶段（工序）。"""
    name: str
    title: str
    needs_gpu: bool
    est_s: int                  # 预估耗时（用于判断"是不是卡住了"）
    run: Callable[[dict], None]  # 入参是 pipeline_run.params


# ==================== 各阶段的实现 ====================
#
# ⚠️ 这里用**函数内 import**，不是偷懒：
#     `hub/cli.py` 需要 import `hub.queue`（为了 `hub worker` 命令），
#     而阶段实现又调 CLI 里的 `cmd_*`，顶部 import 会形成循环。
#     把 import 放到函数里，循环就断开了。

def _stage_ingest(params: dict) -> None:
    """导入导出文件（幂等：按 platform_id upsert）。"""
    from hub.cli import cmd_p0

    files = params.get("files") or [
        {"path": str(DATA_DIR / name), "platform": plat, "type": dtype}
        for name, plat, dtype in DEFAULT_IMPORTS
    ]
    for f in files:
        path = Path(f["path"])
        if not path.exists():
            raise FileNotFoundError(f"导出文件不存在：{path}")
        cmd_p0(str(path), f["platform"], f["type"], save=True)


def _stage_dqe(params: dict) -> None:
    from hub.cli import cmd_dqe
    cmd_dqe(force=True)          # 全量重判（新数据进来了，旧记录也要重看）


def _stage_mm(params: dict) -> None:
    from hub.cli import cmd_mm
    cmd_mm(str(IMAGE_PACKAGE), force=True, vision="ollama")


def _stage_topic(params: dict) -> None:
    from hub.cli import cmd_topic
    cmd_topic()                  # 默认增量：只处理没分类过的


def _stage_align(params: dict) -> None:
    from hub.cli import cmd_align
    cmd_align()


def _stage_cluster(params: dict) -> None:
    from hub.cli import cmd_cluster
    cmd_cluster()


def _stage_price(params: dict) -> None:
    from hub.cli import cmd_price
    cmd_price()


def _stage_priority(params: dict) -> None:
    from hub.cli import cmd_priority
    cmd_priority()


def _stage_propose(params: dict) -> None:
    from hub.cli import cmd_propose
    cmd_propose()


# ==================== 有序阶段表 ====================

STAGES: list[Stage] = [
    Stage("ingest", "导入导出文件", False, 40, _stage_ingest),
    Stage("dqe", "M1 数据降噪", False, 3, _stage_dqe),
    Stage("mm", "M2 多模态（图片）", True, 180, _stage_mm),
    Stage("topic", "M3 归因链", True, 220, _stage_topic),
    Stage("align", "M11 同款对齐", False, 2, _stage_align),
    Stage("cluster", "M4 问题簇聚类", True, 35, _stage_cluster),
    Stage("price", "M4 价格背离", False, 1, _stage_price),
    Stage("priority", "M4 优先级打分", False, 1, _stage_priority),
    Stage("propose", "M5 生成提案 + 过网关", True, 25, _stage_propose),
]

STAGE_NAMES: list[str] = [s.name for s in STAGES]
BY_NAME: dict[str, Stage] = {s.name: s for s in STAGES}


def next_stage_after(name: str | None) -> Stage | None:
    """断点续跑用：`name` 跑完之后下一个是谁？

    :param name: 最后**跑完**的阶段名；None 表示还没开始
    :return: 下一个阶段；没有下一个返回 None（链跑完了）
    """
    if name is None:
        return STAGES[0] if STAGES else None
    if name not in BY_NAME:
        # 未知阶段名（比如降级后改名了）——保守起见从头来，靠幂等兜住
        return STAGES[0] if STAGES else None
    idx = STAGE_NAMES.index(name)
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


def total_est_s() -> int:
    return sum(s.est_s for s in STAGES)
