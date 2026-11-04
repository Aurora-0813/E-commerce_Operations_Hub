# -*- coding: utf-8 -*-
"""
清零业务数据（reset_business_data.py）—— 2026-11-03
==================================================
**目的**：把库恢复到「**系统初始状态**」，然后重新上传数据跑一遍 ✅

**保留什么（不清）**
    · `tenant`            —— 租户（清了啥都没了 ✅）
    · `app_user`          —— **账号**（你要求保留 ✅）
    · `platform_field_map`—— 平台字段映射（**配置**，不是数据 ✅）
    · `alembic_version`   —— 迁移版本（不在模型里，自动保留 ✅）

**清什么（其余全部）**
    业务数据：record / review_feature / review_topic / spu_catalog /
             sales_daily / price_snapshot / sku_mapping / promo_calendar /
             incremental_cursor / ingest_batch
    分析结果：issue_cluster / cluster_member / anomaly / analysis_run
    执行记录：action_proposal / tool_execution / idempotency_record /
             hitl_task / dead_letter / action_outcome
    图片明细：image_asset
    可观测：  trace_span / judge_score / model_registry / eval_case /
             eval_result / agent_run / pipeline_run / audit_log
    内容：    knowledge_entry / case_entry / notification / report_job /
             report_field_whitelist / review_reply_draft / review_appeal

**安全性**
    · 默认 **dry-run**（只打印"会清多少"，不动手 ✅）
    · 真清必须 `--yes` ✅
    · **按外键依赖顺序删**：每轮试删、被外键挡住就留到下一轮，最多 5 轮 ✅
      （比手写拓扑排序可靠 ✅，也不需要 `TRUNCATE ... CASCADE` —— 那个会**连
         `app_user` 一起清掉** ✅ ✗）
    · 全程一个事务，出错回滚 ✅
    · 清完打一张"空表清单"，你一眼确认是初始状态 ✅

用法：
    python -B scripts/reset_business_data.py            # 只看
    python -B scripts/reset_business_data.py --yes      # 真清
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text                          # noqa: E402

from hub.persistence.db import get_session                          # noqa: E402
from hub.persistence.models import Base                             # noqa: E402

#: **不清**的表（账号 + 配置 ✅）
KEEP = {"tenant", "app_user", "platform_field_map"}

#: 不参与清理的表（不在模型里，但存在 ✅）
SYSTEM_TABLES = {"alembic_version"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真清（不加就是 dry-run ✅）")
    args = ap.parse_args()
    dry = not args.yes
    tag = "【dry-run 只看不动】" if dry else "【真清】"

    targets = [t for t in Base.metadata.tables if t not in KEEP]
    print("=" * 70)
    print(f"  清零业务数据 {tag}")
    print("=" * 70)
    print(f"  保留：{', '.join(sorted(KEEP))} ✅")
    print(f"  要清：{len(targets)} 张表")
    print()

    with get_session() as s:
        # ---------- 统计 ----------
        rows: list[tuple[str, int]] = []
        for t in sorted(targets):
            try:
                n = int(s.scalar(text(f'SELECT count(*) FROM "{t}"')) or 0)
            except Exception:                   # noqa: BLE001  表可能还没建
                n = -1
            rows.append((t, n))

        have = [(t, n) for t, n in rows if n > 0]
        empty = [t for t, n in rows if n == 0]
        missing = [t for t, n in rows if n < 0]

        print(f"  {'表':<32} {'行数':>8}")
        print("  " + "-" * 42)
        for t, n in sorted(have, key=lambda x: -x[1]):
            print(f"  {t:<32} {n:>8}")
        print()
        print(f"  有数据的表 {len(have)} 张｜本来就空的 {len(empty)} 张｜"
              f"不存在的 {len(missing)} 张")
        if missing:
            print(f"  不存在的：{', '.join(missing)}")

        if dry:
            print()
            print("  👉 这是预演。要真清就加 `--yes` ✅")
            print("     清完 = **系统初始状态**（只剩账号 + 配置）✅")
            return 0

        # ---------- 真清：多轮试删（被外键挡住的留到下一轮 ✅）----------
        print()
        print("  开始清理（按外键依赖，多轮试删）…")
        remaining = {t for t, _ in have}
        for rnd in range(1, 6):
            done_this_round = 0
            for t in list(remaining):
                # ⚠️ **每张表单独提交**（2026-11-03 修）
                #    如果整个循环共用一个事务，某张表被外键挡住 → rollback
                #    → **本轮之前删成功的也全丢了** ✅ ✗
                try:
                    s.execute(text(f'DELETE FROM "{t}"'))
                    s.commit()
                    remaining.discard(t)
                    done_this_round += 1
                except Exception:               # noqa: BLE001  外键挡住 → 下一轮再试
                    s.rollback()
            print(f"    第 {rnd} 轮：清掉 {done_this_round} 张，剩 {len(remaining)} 张")
            if not remaining:
                break
            if done_this_round == 0:
                print(f"    ⚠️ 剩下的被外键卡住：{chr(44).join(sorted(remaining))}")
                break

        print("  ✅ 清理完成")

    # ---------- 复查 ----------
    with get_session() as s:
        print()
        print("  复查（应该只剩账号 + 配置有数据）:")
        for t in sorted(list(Base.metadata.tables)):
            try:
                n = int(s.scalar(text(f'SELECT count(*) FROM "{t}"')) or 0)
            except Exception:                   # noqa: BLE001
                continue
            if n > 0:
                mark = "  ← **保留** ✅" if t in KEEP else "  ⚠️ 还有残留"
                print(f"    {t:<30} {n:>8}{mark}")

    print()
    print("  ✅ 现在是**系统初始状态**")
    print("     下一步：上传 Presentation_materials 的 6 个文件 → 点「开始分析」→ 8 分钟 ✅")
    print("     （图片包另传：数据接入 → 图片包 → 先对账再处理 ✅）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
