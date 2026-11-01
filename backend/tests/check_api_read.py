# -*- coding: utf-8 -*-
"""
读接口测试（check_api_read.py）—— 阶段 4.4a（8 个基础读）
========================================================
验 10 条：
    [1] **每个接口都要鉴权**：不带 token 一律 401（8 个逐个验 ✅）
    [2] today / status / health 都能返回结构
    [3] /brief 明确说"没生成过"（**不返回假数据** ✅）
    [4] /eval/latest 有/没有数据两种都合理
    [5] /proposals 分页字段齐（total/limit/offset/items）
    [6] /proposals?status= 筛选生效（总数 ≤ 不筛的总数）
    [7] /proposals/{id} 详情含 args/rationale/experience
    [8] /proposals/{坏id} → 400（不是 500 ✅）
    [9] /proposals/{不存在的uuid} → 404
    [10] /audit 分页 + 按 action 筛

第 [1] 条为什么单独列：**未鉴权的接口就是数据泄露** ✅
    所以不是"抽一个验一下"，是**8 个全验** ✅
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
PWD = "123456"

ENDPOINTS = ["/api/today", "/api/status", "/api/health", "/api/brief",
             "/api/eval/latest", "/api/proposals", "/api/audit",
             "/api/proposals/00000000-0000-0000-0000-000000000000"]


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 66)
    print("  读接口（阶段 4.4a）")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 8 个接口全部要鉴权 ----------
    print("\n[1] 8 个接口不带 token 全部 401")
    for ep in ENDPOINTS:
        r = c.get(ep)
        check(f"{ep} → 401", r.status_code == 401, str(r.status_code))

    # 登录
    r = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": PWD})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # ---------- [2] today / status / health ----------
    print("\n[2] today / status / health 有结构")
    r = c.get("/api/today", headers=H)
    check("today 200", r.status_code == 200, str(r.status_code))
    # `Today` 数据类的真实字段（2026-10-31 核对）：must_do / should_do / waiting / anomalies ✅
    check("today 有 must_do 等四个区块",
          all(k in r.json() for k in ("must_do", "should_do", "waiting", "anomalies")),
          str(list(r.json().keys()))[:80])
    r = c.get("/api/status", headers=H)
    check("status 200 且有 sections", r.status_code == 200 and "sections" in r.json(),
          str(r.status_code))
    r = c.get("/api/health", headers=H)
    check("health 200", r.status_code == 200, str(r.status_code))

    # ---------- [3] brief 结构 ----------
    print("\n[3] /brief 结构（有没有日报都要有 available 字段）")
    r = c.get("/api/brief", headers=H)
    check("200", r.status_code == 200, str(r.status_code))
    j = r.json()
    # ⚠️ **不能断言 available=False**（2026-11-01 踩到：
    #    阶段 3.6 做完后，只要跑过 `hub brief` 就有数据 → 断言 False 会假红 ✅）
    #    要断言的是"**结构稳定**"：有没有数据都返回 available，且没有就给 reason ✅
    check("有 available 字段", "available" in j, str(list(j.keys())))
    if j.get("available"):
        check("有数据时 text 非空", bool(j.get("text")), str(j.get("text"))[:40])
    else:
        check("没数据时给 reason", bool(j.get("reason")), str(j.get("reason"))[:50])

    # ---------- [4] eval/latest 两种都合理 ----------
    print("\n[4] /eval/latest")
    r = c.get("/api/eval/latest", headers=H)
    check("200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("有 available 字段", "available" in j, str(list(j.keys())))

    # ---------- [5] proposals 分页 ----------
    print("\n[5] /proposals 分页字段齐")
    r = c.get("/api/proposals?limit=5", headers=H)
    j = r.json()
    check("200", r.status_code == 200, str(r.status_code))
    check("有 total/limit/offset/items",
          all(k in j for k in ("total", "limit", "offset", "items")), str(list(j.keys())))
    total_all = j["total"]

    # ---------- [6] 筛选 ----------
    print("\n[6] /proposals?status= 筛选生效")
    r = c.get("/api/proposals?status=PROPOSED&limit=5", headers=H)
    check("筛选后总数 ≤ 全部",
          r.json()["total"] <= total_all, f"{r.json()['total']} <= {total_all}")

    # ---------- [7] 详情 ----------
    print("\n[7] /proposals/{id} 详情含 args/rationale/experience")
    r = c.get("/api/proposals?limit=1", headers=H)
    items = r.json().get("items") or []
    if not items:
        print("  [!] 库里没有提案，跳过 [7]（先跑 hub propose 或 hub handle --adopt）")
    else:
        pid = items[0]["id"]
        r = c.get(f"/api/proposals/{pid}", headers=H)
        check("200", r.status_code == 200, str(r.status_code))
        j = r.json()
        check("有 args", "args" in j)
        check("有 rationale", "rationale" in j)
        check("有 experience 键（图⑤ 的数据）", "experience" in j, str(list(j.keys())))

    # ---------- [8]/[9] 错误 id ----------
    print("\n[8][9] 坏 id → 400；不存在 → 404（都不是 500）")
    r = c.get("/api/proposals/not-a-uuid", headers=H)
    check("坏 id → 400", r.status_code == 400, str(r.status_code))
    check("400 带 trace_id", bool(r.json().get("trace_id")))
    r = c.get("/api/proposals/00000000-0000-0000-0000-000000000000", headers=H)
    check("不存在 → 404", r.status_code == 404, str(r.status_code))

    # ---------- [10] audit ----------
    print("\n[10] /audit 分页 + 按 action 筛")
    r = c.get("/api/audit?limit=5", headers=H)
    j = r.json()
    check("200 且有 items", r.status_code == 200 and "items" in j, str(r.status_code))
    r = c.get("/api/audit?action=login&limit=5", headers=H)
    check("按 action 筛能返回", r.status_code == 200, str(r.status_code))
    if r.json().get("items"):
        check("筛出来的 action 都是 login",
              all(x["action"] == "login" for x in r.json()["items"]))

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
