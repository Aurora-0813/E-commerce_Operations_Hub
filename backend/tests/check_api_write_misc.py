# -*- coding: utf-8 -*-
"""
写接口 5.3 测试（check_api_write_misc.py）
========================================
验 12 条：
    [1] 5 个接口都要鉴权
    [2] `notify/{id}/read`：不存在 → 404；存在 → 200 + status=READ
    [3] `knowledge` 新建 → **201 + DRAFT**
    [4] **DRAFT 不生效**：建完立刻查提示词段落 → 空 ✅
    [5] `knowledge/{id}/publish`：**operator → 403**（要 supervisor）
    [6] supervisor 发布 → 200 + version=2
    [7] 发布后**段落里能看到** ✅（跨模块验证：API 调用 → 提示词变化 ✅）
    [8] 重复发布 → **409**
    [9] `export/reviews` → 200 + xlsx 内容类型 + 行数头
    [10] `export/xxx`（非法）→ 400
    [11] `dlq/replay`：operator → 403
    [12] `dlq/replay` 不带 confirm → 400
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import (KnowledgeEntry,  # noqa: E402
                                    Notification)

FAILS: list[str] = []
OP = {"email": "operator@hub.local", "password": "123456"}
SUP = {"email": "supervisor@hub.local", "password": "123456"}
TAG = "测试-5.3-"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def login(c, who) -> dict:
    r = c.post("/api/auth/login", json=who)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def cleanup() -> None:
    with get_session() as s:
        s.execute(delete(KnowledgeEntry).where(
            KnowledgeEntry.title.like(TAG + "%")))
        s.execute(delete(Notification).where(
            Notification.resource_id.like("test-5.3-%")))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  写接口 5.3：通知/知识/导出/死信")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)
    cleanup()

    # ---------- [1] 鉴权 ----------
    print("\n[1] 5 个接口不带 token 全部 401")
    for method, ep in (("post", "/api/notify/1/read"), ("post", "/api/knowledge"),
                       ("post", "/api/knowledge/x/publish"),
                       ("get", "/api/export/reviews"), ("post", "/api/dlq/replay")):
        r = getattr(c, method)(ep)
        check(f"{ep} → 401", r.status_code == 401, str(r.status_code))

    HOP, HSUP = login(c, OP), login(c, SUP)

    # ---------- [2] notify read ----------
    print("\n[2] notify/{id}/read")
    r = c.post("/api/notify/999999999/read", headers=HOP)
    check("不存在 → 404", r.status_code == 404, str(r.status_code))
    # 造一条通知 ✅
    with get_session() as s:
        n = Notification(event_type="test", resource_id="test-5.3-n1",
                         channel="webhook", recipient="",
                         payload={"items": []}, status="PENDING", attempts=0)
        s.add(n)
        s.flush()
        nid = int(n.id)
        s.commit()
    r = c.post(f"/api/notify/{nid}/read", headers=HOP)
    check("200", r.status_code == 200, str(r.status_code))
    check("现在 status=READ", r.json().get("now") == "READ", str(r.json()))

    # ---------- [3][4] knowledge create ----------
    print("\n[3][4] knowledge 新建 → 201 DRAFT；**DRAFT 不生效**")
    r = c.post("/api/knowledge", json={"title": TAG + "质量口径",
                                       "content": "先道歉再解释 ✅",
                                       "topic": "quality"}, headers=HOP)
    check("201", r.status_code == 201, str(r.status_code))
    kid = (r.json() or {}).get("id")
    check("返回 DRAFT", (r.json() or {}).get("status") == "DRAFT")
    from hub.knowledge import render_patches
    txt, _ = render_patches("quality")
    check("**DRAFT 时提示词段落里没有它**", TAG not in txt, txt[:40])

    # ---------- [5][6][7][8] publish ----------
    print("\n[5][6][7][8] publish：operator 403 / supervisor 200 / 生效 / 重复 409")
    r = c.post(f"/api/knowledge/{kid}/publish", headers=HOP)
    check("operator → 403", r.status_code == 403, str(r.status_code))
    r = c.post(f"/api/knowledge/{kid}/publish", headers=HSUP)
    check("supervisor → 200", r.status_code == 200, str(r.status_code))
    check("version=2", (r.json() or {}).get("version") == 2,
          str((r.json() or {}).get("version")))
    txt2, h2 = render_patches("quality")
    check("**发布后段落里能看到它** ✅", TAG in txt2, txt2[:60].replace("\n", " "))
    r = c.post(f"/api/knowledge/{kid}/publish", headers=HSUP)
    check("重复发布 → 409", r.status_code == 409, str(r.status_code))

    # ---------- [9][10] export ----------
    print("\n[9][10] export：reviews 能下 / 非法 kind → 400")
    r = c.get("/api/export/reviews", headers=HOP)
    check("200", r.status_code == 200, str(r.status_code))
    check("xlsx 内容类型",
          "spreadsheetml" in (r.headers.get("content-type") or ""),
          r.headers.get("content-type") or "")
    check("有 X-Export-Rows 头", "x-export-rows" in {k.lower() for k in r.headers},
          str({k: v for k, v in r.headers.items() if k.lower().startswith("x-")}))
    check("有 X-Export-Pii-Redacted 头",
          "x-export-pii-redacted" in {k.lower() for k in r.headers})
    check("内容不是空的", len(r.content) > 1000, f"{len(r.content)} 字节")
    r = c.get("/api/export/nonsense", headers=HOP)
    check("非法 kind → 400", r.status_code == 400, str(r.status_code))

    # ---------- [11][12] dlq replay ----------
    print("\n[11][12] dlq/replay：operator 403 / 不带 confirm 400")
    r = c.post("/api/dlq/replay", json={"confirm": True, "run_id_prefix": "abcd1234"},
               headers=HOP)
    check("operator → 403", r.status_code == 403, str(r.status_code))
    r = c.post("/api/dlq/replay", json={"confirm": False, "run_id_prefix": "abcd1234"},
               headers=HSUP)
    check("不带 confirm → 400", r.status_code == 400, str(r.status_code))
    check("code=CONFIRM_REQUIRED", r.json().get("code") == "CONFIRM_REQUIRED",
          str(r.json().get("code")))

    cleanup()
    print("\n（测试数据已清理 ✅）")
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
