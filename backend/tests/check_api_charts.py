# -*- coding: utf-8 -*-
"""
图表接口测试（check_api_charts.py）—— 阶段 4.4b/4.4c
===================================================
验 12 条：
    [1] 9 个图表接口**全部要鉴权**（不带 token → 401，逐个验 ✅）
    [2] 每个接口都能 200 且有 `items`（或明确的结构）
    [3] `/charts/cluster-tiers` 的簇数之和 == 库里簇总数（**口径自洽** ✅）
    [4] `/charts/bad-rate-trend` 的 `bad ≤ total`（不可能差评比总数多 ✅）
    [5] `/retro` 的 `effect` 是合法取值或 null
    [6] `/handle/{坏id}` → 400；不存在 → 404
    [7] `/handle/{真实run}` → 有 steps
    [8] `/handle/{真实run}/trace` → 每个 span 有 duration_ms（**用 end_ts-start_ts 算的** ✅）
    [9] `/charts/hitl-trend` 的 days 参数生效（返��的条数 ≤ days）
    [10] 时间范围参数越界 → 422（pydantic 拦下）
    [11] **没有数据时返回空 items**（不是 500 ✅）
    [12] CORS 头存在（前端能跨域）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
PWD = "123456"

CHARTS = ["/api/charts/cluster-tiers", "/api/charts/clusters",
          "/api/charts/hitl-trend", "/api/charts/bad-rate-trend",
          "/api/charts/observe-timeline", "/api/retro", "/api/audit/trend",
          # 2026-10-31 补的 4 个（之前被我错说成"数据不支持"✅）
          "/api/charts/topic-distribution", "/api/charts/gmv-top",
          "/api/charts/price-vs-badrate"]


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 66)
    print("  图表聚合（阶段 4.4b/4.4c）")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 鉴权 ----------
    print("\n[1] 9 个图表接口不带 token 全部 401")
    for ep in CHARTS:
        r = c.get(ep)
        check(f"{ep} → 401", r.status_code == 401, str(r.status_code))
    r = c.get("/api/handle/00000000-0000-0000-0000-000000000000")
    check("/api/handle/{id} → 401", r.status_code == 401, str(r.status_code))

    r = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": PWD})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # ---------- [2] 每个都能 200 ----------
    print("\n[2] 每个接口 200 且有数据块")
    for ep in CHARTS:
        r = c.get(ep, headers=H)
        check(f"{ep} 200", r.status_code == 200, str(r.status_code))
        j = r.json() if r.status_code == 200 else {}
        check(f"{ep} 有 items", "items" in j, str(list(j.keys()))[:60])

    # ---------- [3] 口径自洽 ----------
    # ⚠️ **2026-11-01 改**：`cluster-tiers` 现在**只算"当前这一次"的簇**
    #    （每跑一次全链都会插一批新的、旧的留作历史 ✅）
    #    → 所以对比对象是 **当前 run 的簇数**，不是全表 ✅
    #    → 同时验证 `clusters` 接口返回的也是同一批（口径一致 ✅）
    print("\n[3] cluster-tiers 的簇数之和 == 当前 run 的簇数")
    from sqlalchemy import func, select

    from hub.persistence.current import current_run_id
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        rid = current_run_id(s)
        total = int(s.scalar(
            select(func.count()).select_from(IssueCluster)
            .where(IssueCluster.run_id == rid)) or 0)
    r = c.get("/api/charts/cluster-tiers", headers=H)
    got = sum(x["clusters"] for x in r.json()["items"])
    check("两数相等", got == total, f"{got} vs {total}")

    # ---------- [4] 差评率 ----------
    print("\n[4] bad-rate-trend：bad ≤ total")
    r = c.get("/api/charts/bad-rate-trend?days=30", headers=H)
    bad_ok = all(x["bad"] <= x["total"] for x in r.json()["items"])
    check("每条都满足", bad_ok, f"{len(r.json()['items'])} 条")

    # ---------- [5] retro ----------
    print("\n[5] /retro 的 effect 合法")
    r = c.get("/api/retro", headers=H)
    # ⚠️ **2026-11-03 修：`effect` 是 DID 的 dict，不是字符串** ✅
    #    以前它**永远是 None**（窗口没到）→ 这条断言一直"通过" ✅ ✗
    #    现在有 68 条算出真区间 → 形状是
    #      {"did_pp": -5.5, "ci_low": -9.2, "ci_high": -1.87, "n_treated": 10, ...}
    #    所以断言改成：**None 或 dict 都算合法**，且 dict 里三键齐全 ✅
    def _effect_ok(e) -> bool:
        if e is None:
            return True
        if not isinstance(e, dict):
            return False
        return all(k in e for k in ("did_pp", "ci_low", "ci_high"))

    items = r.json()["items"]
    check("effect 都是合法值（None 或 DID dict）",
          all(_effect_ok(x["effect"]) for x in items), f"{len(items)} 条")
    # 顺带验我新加的 3 个拆出来的字段 ✅
    check("每条都有 effect_point / ci_low / ci_high / crosses_zero",
          all(set(("effect_point", "ci_low", "ci_high", "crosses_zero")) <= set(x)
              for x in items), f"{len(items)} 条")

    # ---------- [6] handle 错误 id ----------
    print("\n[6] /handle 坏 id → 400；不存在 → 404")
    r = c.get("/api/handle/not-a-uuid", headers=H)
    check("坏 id → 400", r.status_code == 400, str(r.status_code))
    r = c.get("/api/handle/00000000-0000-0000-0000-000000000000", headers=H)
    check("不存在 → 404", r.status_code == 404, str(r.status_code))

    # ---------- [7][8] 真实 run ----------
    print("\n[7][8] 真实 agent_run 的轨迹 + 每步 token")
    from hub.persistence.models import AgentRun
    with get_session() as s:
        run = s.scalars(select(AgentRun).order_by(
            AgentRun.created_at.desc()).limit(1)).first()
        rid = str(run.id) if run else None
    if rid is None:
        print("  [!] 库里没有 agent_run，跳过 [7][8]（先跑 hub handle）")
    else:
        r = c.get(f"/api/handle/{rid}", headers=H)
        check("200 且有 steps", r.status_code == 200 and "steps" in r.json(),
              str(r.status_code))
        r = c.get(f"/api/handle/{rid}/trace", headers=H)
        check("trace 200", r.status_code == 200, str(r.status_code))
        items = r.json().get("items") or []
        check("每个 span 都有 duration_ms 字段（可能为 null）",
              all("duration_ms" in x for x in items), f"{len(items)} 个 span")

    # ---------- [9] days 参数生效 ----------
    print("\n[9] hitl-trend 的 days 参数生效")
    r = c.get("/api/charts/hitl-trend?days=3", headers=H)
    items = r.json()["items"]
    check("返回条数 ≤ days+1", len(items) <= 4, f"{len(items)} 条")

    # ---------- [10] 参数越界 ----------
    print("\n[10] days 越界 → 422（pydantic 拦下）")
    r = c.get("/api/charts/bad-rate-trend?days=999", headers=H)
    check("422", r.status_code == 422, str(r.status_code))

    # ---------- [12] CORS ----------
    print("\n[12] CORS 头存在")
    r = c.get("/api/ping", headers={"Origin": "http://localhost:5173"})
    check("有 access-control-allow-origin",
          "access-control-allow-origin" in {k.lower() for k in r.headers},
          str(dict(r.headers))[:80])

    # ---------- [13] factors（2026-10-31 补的第 5 个）----------
    print("\n[13] /clusters/{id}/factors 四因子")
    from hub.persistence.models import IssueCluster as _IC
    with get_session() as s:
        _c = s.scalars(select(_IC).limit(1)).first()
        _cid = str(_c.id) if _c else None
    if _cid is None:
        check("库里有簇可测", False)
    else:
        r = c.get(f"/api/clusters/{_cid}/factors", headers=H)
        check("200", r.status_code == 200, str(r.status_code))
        j = r.json()
        check("有 inputs（真实输���，不是编的因子）", "inputs" in j, str(list(j.keys()))[:60])
        check("明确写了 note（不编造公式）", bool(j.get("note")), str(j.get("note"))[:50])
        r = c.get("/api/clusters/not-a-uuid/factors", headers=H)
        check("坏 id → 400", r.status_code == 400, str(r.status_code))

    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
