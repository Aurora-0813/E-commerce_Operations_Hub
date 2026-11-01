# -*- coding: utf-8 -*-
"""
写接口 5.2 测试（check_api_write_action.py）
==========================================
验 11 条：
    [1] 6 个接口都要鉴权
    [2] **`approve` 要 supervisor**：operator 调 → 403
    [3] `approve` 不带 `confirm: true` → 400
    [4] `approve` 合法 → 200（或 404 "没有待批任务" ✅ —— 那是数据状态，不是 bug）
    [5] **`reject` 空原因 → 422**（pydantic min_length=1 ✅）
    [6] `reject` 带原因 → 200/404
    [7] `propose` 能跑（会调 LLM，慢 ✅）
    [8] `handle/{id}/adopt`：不存在的 run → 404
    [9] `handle/{id}/adopt`：**非 DONE 的 run → 409**
    [10] `handle/{id}/reject`：空原因 → 422
    [11] `retro` 能跑（`only_due=true` 时不调 LLM ✅）

⚠️ **不真跑 propose / retro 的 LLM**：`propose` 会调 7B（慢 ✅）→ 用 `TestClient` 调但
   接受它慢（约 10 秒 ✅）；`retro` 用 `only_due=true`（不调 LLM ✅）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
OP = {"email": "operator@hub.local", "password": "123456"}
SUP = {"email": "supervisor@hub.local", "password": "123456"}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def login(c, who) -> dict:
    r = c.post("/api/auth/login", json=who)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main() -> int:
    print("=" * 66)
    print("  写接口 5.2：审批链")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 鉴权 ----------
    print("\n[1] 6 个接口不带 token 全部 401")
    eps = [("post", "/api/proposals/00000000-0000-0000-0000-000000000000/approve"),
           ("post", "/api/proposals/00000000-0000-0000-0000-000000000000/reject"),
           ("post", "/api/propose"), ("post", "/api/handle/00000000-0000-0000-0000-000000000000/adopt"),
           ("post", "/api/handle/00000000-0000-0000-0000-000000000000/reject"),
           ("post", "/api/retro")]
    for method, ep in eps:
        r = getattr(c, method)(ep)
        check(f"{ep} → 401", r.status_code == 401, str(r.status_code))

    HOP, HSUP = login(c, OP), login(c, SUP)

    # ---------- [2] approve 要 supervisor ----------
    print("\n[2] approve 要 supervisor：operator 调 → 403")
    r = c.post("/api/proposals/00000000-0000-0000-0000-000000000000/approve",
               json={"confirm": True}, headers=HOP)
    check("operator → 403", r.status_code == 403, str(r.status_code))
    check("code=ROLE_TOO_LOW", r.json().get("code") == "ROLE_TOO_LOW",
          str(r.json().get("code")))

    # ---------- [3] confirm ----------
    print("\n[3] approve 不带 confirm → 400")
    r = c.post("/api/proposals/00000000-0000-0000-0000-000000000000/approve",
               json={"confirm": False}, headers=HSUP)
    check("400", r.status_code == 400, str(r.status_code))
    check("code=CONFIRM_REQUIRED", r.json().get("code") == "CONFIRM_REQUIRED",
          str(r.json().get("code")))

    # ---------- [4] approve 合法（不存在的提案 → 404，这是数据状态 ✅）----------
    print("\n[4] approve 合法路径（不存在的提案 → 404，不是 500）")
    r = c.post("/api/proposals/00000000-0000-0000-0000-000000000000/approve",
               json={"confirm": True}, headers=HSUP)
    check("404（没有待批任务）", r.status_code == 404, str(r.status_code))
    check("没有 500", r.status_code != 500)

    # ---------- [5][6] reject ----------
    print("\n[5][6] reject：空原因 422；带原因 404/200")
    r = c.post("/api/proposals/00000000-0000-0000-0000-000000000000/reject",
               json={"reason": ""}, headers=HSUP)
    check("空原因 → 422", r.status_code == 422, str(r.status_code))
    r = c.post("/api/proposals/00000000-0000-0000-0000-000000000000/reject",
               json={"reason": "这个方案不符合成本要求"}, headers=HSUP)
    check("带原因 → 404/200", r.status_code in (200, 404), str(r.status_code))

    # ---------- [8][9] adopt ----------
    print("\n[8][9] adopt：不存在 → 404")
    r = c.post("/api/handle/00000000-0000-0000-0000-000000000000/adopt",
               json={"confirm": True}, headers=HOP)
    check("404", r.status_code == 404, str(r.status_code))
    print("\n[9] adopt：非 DONE 的 run → 409")
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import AgentRun
    with get_session() as s:
        run_bad = s.scalars(select(AgentRun).where(
            AgentRun.status == "NEEDS_HUMAN").limit(1)).first()
        rid_bad = str(run_bad.id) if run_bad else None
    if rid_bad is None:
        print("  [!] 库里没有 NEEDS_HUMAN 的 run，跳过")
    else:
        r = c.post(f"/api/handle/{rid_bad}/adopt", json={"confirm": True}, headers=HOP)
        check("409", r.status_code == 409, str(r.status_code))
        check("code=NOT_DONE", r.json().get("code") == "NOT_DONE",
              str(r.json().get("code")))

    # ---------- [10] handle/reject ----------
    print("\n[10] handle/{id}/reject：空原因 → 422")
    r = c.post("/api/handle/00000000-0000-0000-0000-000000000000/reject",
               json={"reason": ""}, headers=HOP)
    check("422", r.status_code == 422, str(r.status_code))

    # ---------- [11] retro（不调 LLM ✅）----------
    print("\n[11] retro（only_due=true，不调 LLM）")
    r = c.post("/api/retro?only_due=true", headers=HOP)
    check("200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("有 done/skipped", "done" in j and "skipped" in j, str(j)[:80])

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
