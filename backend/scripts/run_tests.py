# -*- coding: utf-8 -*-
"""
测试统一入口（run_tests.py）—— 阶段 2.3
=======================================
**为什么需要它**：`tests/` 下有 50 个 `check_*.py`，各跑各的 ✅
   ↓
于是出现两个真实问题：
   · 新人不知道该跑哪些（跑全部要 10 分钟，挑着跑又不知道挑哪些）
   · **我自己也会漏跑**（改完只跑相关的那几个，别的退化不知道）
   ↓
**"我全测过了"这句话，之前是没有办法证明的** ✅

三组（按"要不要 LLM / 是不是探针"分）：

    quick  不调 LLM、不依赖 GPU。秒级。**改完代码至少跑这一组**。
    full   quick + 需要 LLM 的（分类/归因/聚类/网关/队列…）。分钟级。
    probe  调试用的探针脚本（打印原始输出看现象），**不进 CI**。
           它们不是"测试"，是"诊断工具"——混在测试里会让人以为覆盖了很多。

判定（为什么不只看退出码）：
    `tests/` 里有一批早期脚本没有"失败即非零退出"的约定 ✅
    （它们只 print，退出码永远是 0 ✅）
    → 所以同时看两个信号：**退出码 ≠ 0** 或 **输出里出现 ❌ / [FAIL] / FAIL**
    → 这是对"混合质量的测试语料"的务实做法 ✅
      （长远应该把老脚本改成断言式，那是债台账之外的活）

跑法：
    python scripts/run_tests.py                 # 默认 quick
    python scripts/run_tests.py --group full
    python scripts/run_tests.py --group probe
    python scripts/run_tests.py --list          # 看分组
    python scripts/run_tests.py --match gateway # 只跑名字含 gateway 的
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # backend/
TESTS = ROOT / "tests"

# ---------------------------------------------------------------- 分组
# 原则：
#   quick —— 纯逻辑/契约/配置，不碰 LLM、不碰 GPU。**任何改动后都该跑**。
#   full  —— 需要真实 LLM 或数据库状态的（跑一次要几十秒到几分钟）。
#   probe —— 调试探针：打印原始输出给人看，不做断言。不进 CI。
GROUPS: dict[str, list[str]] = {
    "quick": [
        "check_env",              # 环境变量/配置能读
        "check_db",              # 数据库能连（不写数据）
        "check_ui",              # 终端输出适配（GBK 不能崩）
        "check_contracts",       # 契约层：每个模块都被用或标了 PLANNED
        "check_cloud_guard",     # 出网闸门：图片/原文/聚合/未知类别
        "check_readonly_tools",  # 阶段 3.1：5 个只读工具（只读 + 截断 + 负向）
        "check_agent_handle",    # 阶段 3.4：agent 主路径（用假 LLM，不花钱）
        "check_mcp_server",      # 阶段 3.2：MCP server（经网关，L3 不执行）
        "check_critic",          # 阶段 3.6：Critic（三类问题 + 不拦结论）
        "check_agent_guard",     # 阶段 3.7：六项运行约束（步数/token/超时/并发/幂等/升级）
        "check_agent_wiring",    # 阶段 3.7：**主路径真的用了它们吗**（防"函数写了没接线"）
        "check_cloud_audit",     # 阶段 1.2/1.3：出云写 audit_log + 每步 token
        "check_normalize",       # 文本归一化
        "check_simhash",         # SimHash 去重
        "check_rules",           # DQE 规则
        "check_experience_rules",  # 经验回灌的非对称门槛
        "check_risk_floor",      # 风险下限只能升不能降
        "check_experience",      # 历史战绩的聚合与判定
        "check_mock_price",      # 价格数据的合理性
        "check_counts",          # 表行数对得上（读库）
    ],
    "full": [
        "check_llm_client",      # 模型客户端（要 LLM）
        "check_import",          # 多源导入
        "check_ml",              # DQE 模型训练/推理
        "check_fusion",          # 规则+模型融合
        "check_engine",          # DQE 引擎端到端
        "check_accuracy",        # 分类准确率（要 LLM）
        "check_multimodal",      # 图片打标（要视觉模型）
        "check_grounding",       # 证据子串校验（要 LLM）
        "check_align",           # 跨平台同款对齐
        "check_classify",        # 差评分类
        "check_cluster",         # 聚类
        "check_price",           # 价格背离
        "check_tools",           # 工具注册表
        "check_gateway",         # 网关四道防线
        "check_execute",         # 执行链/回查/补偿
        "check_trace",           # 链路追踪
        "check_queue",           # 队列（要 Redis）
        "check_queue_multi",     # 多 worker
        "check_runs",            # 流水线状态机
        "check_multi_label",     # 多标签分类
        "check_multi_topic",     # 多主题
        "check_topic_repo",      # 主题读写
        "check_advice_wiring",   # 经验回灌接线
    ],
    "probe": [
        "check_vision_raw",      # 打印视觉模型原始输出
        "check_mm_sample",       # 多模态抽样
        "check_text_hint",       # 文本提示实验
        "check_attribute",       # 归因单例
        "check_attribute_debug", # 归因调试
        "check_topic_result",    # 主题结果
        "check_exif",            # EXIF 检查
        "check_image_cover",     # 图片覆盖
        "check_embed_vs_tfidf",  # 向量 vs TF-IDF 对比
        "check_two_stage",       # 两阶段实验
        "check_multi_label_p4",  # 多标签 P4
        "check_multilabel_db",   # 多标签落库
        "check_reply_gen",       # 回复生成单例
        "check_trace_min",       # 最小 trace
        "check_llm_text",        # 文本模型单例（手动看输出）
        "check_vision_tag",      # 视觉打标单例（手动看输出）
    ],
}

# 判定"输出里有没有失败标记"。
#
# ⚠️ 第一版写的是 `❌|\[FAIL\]|\bFAIL\b` —— 结果 `check_ui` 被误判为失败 ✅ ✗
#    因为它的**测试数据本身就是 `❌`**（它在验证"❌ 会被替换成 [X]"）✅
#    → 所以不能匹配裸的 ❌，只认"**结尾的失败声明**"。
#
# 现在认三种：
#    · `[FAIL] xxx`        新的断言式脚本（check_contracts / check_cloud_guard 等）
#    · `有失败用例` / `失败 N`（N>0）  老脚本的结尾汇总
#    · `FAILED`
# 而 `通过 10｜失败 0` 这种**成功**的汇总不会被匹配（`失败 0` 被排除）✅
FAIL_PAT = re.compile(r"\[FAIL\]|有失败用例|\bFAILED\b|失败 [1-9]\d*\b")
# 反例（必须不匹配）：「通过 10｜失败 0」「❌ → [X]」「失败 0」


def discover() -> list[str]:
    """tests/ 下所有 check_*.py 的名字（不含 .py）。"""
    return sorted(p.stem for p in TESTS.glob("check_*.py"))


def covered() -> set[str]:
    return {n for names in GROUPS.values() for n in names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="quick", choices=["quick", "full", "probe", "all"])
    ap.add_argument("--match", default=None, help="只跑名字包含该子串的")
    ap.add_argument("--list", action="store_true", help="列出分组，不执行")
    ap.add_argument("--timeout", type=int, default=600, help="单个脚本超时秒数")
    a = ap.parse_args()

    all_files = discover()

    if a.list:
        print(f"tests/ 下共 {len(all_files)} 个 check_*.py\n")
        for g, names in GROUPS.items():
            print(f"[{g}] {len(names)} 个：{', '.join(names)}\n")
        missing = sorted(set(all_files) - covered())
        print(f"未被分组（{len(missing)} 个）：{', '.join(missing) if missing else '无'}")
        return 0

    if a.group == "all":
        names = list(all_files)
    elif a.group == "full":
        # full = quick + full（quick 是 full 的子集，跑全量时不该漏掉纯逻辑测试）
        names = GROUPS["quick"] + GROUPS["full"]
    else:
        names = GROUPS[a.group]

    if a.match:
        names = [n for n in names if a.match in n]

    # 只跑实际存在的（分组表可能写了但文件被删/改名）
    todo = [n for n in names if (TESTS / f"{n}.py").exists()]
    gone = [n for n in names if not (TESTS / f"{n}.py").exists()]
    if gone:
        print(f"⚠️ 分组表里有 {len(gone)} 个文件不存在（已跳过）：{', '.join(gone)}\n")

    print("=" * 66)
    print(f"  测试组：{a.group}　共 {len(todo)} 个脚本")
    print("=" * 66)

    failed: list[tuple[str, str]] = []
    t_all = time.time()
    # ------------------------------------------------------------------
    # ⚠️ 必须给子进程设 UTF-8 环境，否则**所有带 emoji 的测试都会挂** ✅
    #
    # 踩过（2026-10-31）：第一版没设，13 个 quick 测试里 8 个报
    #     UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
    # 原因不是测试坏了，是 **stdout 被 capture_output 变成管道后，
    # Python 用 locale 编码（GBK）而不是终端编码** ✅
    #
    # 这正是 `hub/core/ui.py` 存在的理由（它给 CLI 装了 SafeStream），
    # 但 `tests/check_*.py` 是独立脚本，没装 —— 所以在这里统一兜住 ✅
    # （长远应该给每个 check_*.py 也装 ui.install()，那是另一笔债）
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # ------------------------------------------------------------------
    for i, name in enumerate(todo, 1):
        path = TESTS / f"{name}.py"
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=a.timeout,
                env=env,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
            reason = ""
            if code != 0:
                reason = f"退出码 {code}"
            elif FAIL_PAT.search(out):
                reason = "输出里有 FAIL 标记"
        except subprocess.TimeoutExpired:
            out, code, reason = "", -1, f"超时（>{a.timeout}s）"

        dt = time.time() - t0
        if reason:
            failed.append((name, reason))
            print(f"  [{i}/{len(todo)}] ❌ {name}　{dt:.1f}s　{reason}")
            # 失败时把尾部 15 行打出来 —— 不然还要手动去跑一次才知道为什么
            tail = "\n".join(out.strip().splitlines()[-15:])
            if tail:
                print("      " + "\n      ".join(tail.splitlines()))
        else:
            print(f"  [{i}/{len(todo)}] ✅ {name}　{dt:.1f}s")

    total = time.time() - t_all
    print("\n" + "=" * 66)
    print(f"  通过 {len(todo) - len(failed)}/{len(todo)}　总耗时 {total:.1f}s")

    # ---- 收尾：清掉测试造的业务数据 ----
    # 为什么必须做（2026-10-31 踩到）：
    #   `check_gateway` 为了测"L2 转审批"会**故意建提案 + 待批任务** ✅
    #   跑一轮就在 `hub today` 的「要我做」里留下几条假待办 ✅ ✗
    #   → 跑得越多越脏，而且会**掩盖真正的异常待办** ✅
    # clean_test_data.py 只按 trace_id 前缀删（gw-test / mcp- / …），
    # 并且会复查"有没有命中真实数据的保护前缀"✅
    print("\n【收尾】清理测试造的业务数据")
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "clean_test_data.py"), "--apply"],
            cwd=str(ROOT), check=False, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        # 只打"结果那一行"（不刷屏）
        # 踩过：原来取"最后一行非空"，结果取到了分隔线 `=====` ✅ ✗
        # → 改成找**指定的关键词行** ✅
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        hit = next((ln for ln in lines if "已删除" in ln or "没有命中" in ln
                    or "禁止删除" in ln), "")
        print(f"  {hit or '（无输出）'}")
        # 退出码 2 = 命中保护前缀（**禁止删除**）→ 这是要人看的信号 ✅
        if proc.returncode == 2:
            print("  🛑 **清理被拦下**：命中了真实数据的前缀，请看 clean_test_data.py 的输出")
    except Exception as e:                      # noqa: BLE001
        # 清理失败不影响测试结论（测试本身已经跑完了）
        print(f"  ⚠️ 清理失败（不影响测试结论）：{type(e).__name__}: {e}")
    if failed:
        print("\n  失败清单：")
        for name, reason in failed:
            print(f"    ❌ {name}　{reason}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
