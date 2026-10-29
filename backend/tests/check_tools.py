# -*- coding: utf-8 -*-
"""
检查点 A：工具真的能改状态吗？改完能查回来吗？改坏了能补偿吗？

验 6 件事：
    ① 注册表里 5 个工具都在，风险等级正确
    ② L0 查价能读到
    ③ L2 改描述 → 回查一致 → 补偿恢复 → 回查一致
    ④ L3 改价 → 回查一致 → 留了价格快照（**改完手动还原，不影响 M4**）
    ⑤ L1 写回复草稿 → 回查一致
    ⑥ 未注册的工具 → get() 返回 None（网关据此拒绝）

运行（在 backend/ 目录下，需要已经跑过 M3/M4）：
    python tests/check_tools.py
"""
import sys
from decimal import Decimal
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select                    # noqa: E402

import hub.tools                                 # noqa: E402,F401  (导入即注册)
from hub.persistence.db import get_session        # noqa: E402
from hub.persistence.models import (              # noqa: E402
    ClusterMember,
    PriceSnapshot,
    SpuCatalog,
)
from hub.tools.spec import (                      # noqa: E402
    CallContext,
    RiskLevel,
    all_specs,
    get,
)

SPU = ("taobao", "TB-0001")
FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def run(tool_name: str, args: dict, ctx: CallContext) -> tuple[dict, bool]:
    """跑一次工具：handler → verify。返回 (结果, 回查是否通过)。"""
    spec = get(tool_name)
    assert spec is not None, f"工具没注册：{tool_name}"
    result = spec.handler(args, ctx)
    vr = spec.verify(args, ctx, result)
    return result, vr.ok


def main() -> None:
    ctx = CallContext(role="manager")      # 测试用最高角色，避免权限挡住

    # ---------- ① 注册表 ----------
    print("=== ① 注册表 ===")
    specs = all_specs()
    for s in specs:
        print(f"  {s.risk_level}  {s.name:<24} 角色={s.required_role or '-':<9}"
              f" 补偿={s.compensate or '无'}")
    by_risk = {lv: [s.name for s in specs if s.risk_level == lv] for lv in RiskLevel.ALL}
    check("L0~L3 四个等级都覆盖",
          all(by_risk[lv] for lv in RiskLevel.ALL),
          f"L0={len(by_risk['L0'])} L1={len(by_risk['L1'])} "
          f"L2={len(by_risk['L2'])} L3={len(by_risk['L3'])}")
    check("未注册的工具返回 None", get("no.such_tool") is None)

    # ---------- ② L0 查价 ----------
    print("\n=== ② L0 查价 ===")
    price_before = None
    res, ok = run("catalog.query_price", {"platform": SPU[0], "spu_id": SPU[1]}, ctx)
    price_before = res.get("price")
    print(f"  当前价：{res.get('title')} = {price_before}")
    check("L0 查价 + 回查", ok and price_before is not None)

    # ---------- ③ L2 改描述 + 补偿 ----------
    print("\n=== ③ L2 改描述 → 回查 → 补偿 ===")
    new_desc = "【测试】面料：95% 棉 5% 氨纶；建议按平时尺码购买，偏小半码。"
    res, ok = run("listing.update_desc",
                  {"platform": SPU[0], "spu_id": SPU[1], "description": new_desc}, ctx)
    check("改完回查一致", ok)
    old_desc = res.get("old_description")
    print(f"  旧描述：{(old_desc or '(空)')[:40]}")
    print(f"  新描述：{new_desc[:40]}")

    # 补偿回旧值
    res2, ok2 = run("listing.restore_desc",
                    {"platform": SPU[0], "spu_id": SPU[1], "description": old_desc}, ctx)
    check("补偿后回查一致", ok2)
    # 再确认数据库里真的回去了
    with get_session() as s:
        row = s.scalars(select(SpuCatalog)
                        .where(SpuCatalog.canonical_spu_id == SPU[1])).first()
        check("数据库里描述已恢复", row.description == old_desc)

    # ---------- ④ L3 改价 + 快照 ----------
    print("\n=== ④ L3 改价 → 回查 → 留快照 ===")
    target = (Decimal(price_before) * Decimal("0.95")).quantize(Decimal("0.01"))
    res, ok = run("product.update_price",
                  {"platform": SPU[0], "spu_id": SPU[1], "new_price": str(target)}, ctx)
    check("改价后回查一致", ok, f"{res.get('old_price')} → {res.get('new_price')}")

    with get_session() as s:
        n = len(s.scalars(select(PriceSnapshot)
                          .where(PriceSnapshot.canonical_spu_id == SPU[1])
                          .where(PriceSnapshot.source == "tool:product.update_price")
                          ).all())
    check("留下了价格快照", n >= 1, f"{n} 条")

    # **手动还原**：测试不能污染 M4 的价格背离结果
    with get_session() as s:
        row = s.scalars(select(SpuCatalog)
                        .where(SpuCatalog.canonical_spu_id == SPU[1])).first()
        row.price_ref = Decimal(price_before)
        s.commit()
    with get_session() as s:
        row = s.scalars(select(SpuCatalog)
                        .where(SpuCatalog.canonical_spu_id == SPU[1])).first()
        check("测试已还原价格", str(row.price_ref) == str(Decimal(price_before)),
              f"现在 = {row.price_ref}")

    # ---------- ⑤ L1 写回复草稿 ----------
    print("\n=== ⑤ L1 写回复草稿 ===")
    with get_session() as s:
        members = s.scalars(select(ClusterMember).limit(3)).all()
    if not members:
        check("有簇成员可用", False, "cluster_member 是空的，先跑 hub cluster")
    else:
        cluster_id = str(members[0].cluster_id)
        review_ids = [str(m.record_id) for m in members]
        content = ("【测试】您反馈的面料扎人问题我们已经记录，会反馈给面料供应商核实。"
                   "需要退换的话联系客服就行。")
        res, ok = run("review.draft_reply", {
            "cluster_id": cluster_id,
            "review_ids": review_ids,
            "content": content,
            "compliance_flags": [],
        }, ctx)
        check("草稿写入并回查一致", ok, f"draft_id={res.get('draft_id')}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ 检查点 A 通过：工具能改状态、能回查、能补偿")
    print("\n注意：价格已手动还原，M4 的价格背离结果不受影响。"
          "\n      描述已补偿回旧值。草稿是真写进去的（下次 reset 会清掉）。")


if __name__ == "__main__":
    main()
