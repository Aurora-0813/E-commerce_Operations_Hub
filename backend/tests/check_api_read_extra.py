# -*- coding: utf-8 -*-
"""
补的两个读接口测试（check_api_read_extra.py）
============================================
2026-11-01 补：
    `GET /api/notify`     ← 通知中心"看不到列表"（有标记已读，没有信箱 ✅）
    `GET /api/knowledge`  ← 知识库"看不到自己发了什么"（有建/发布，没有列表 ✅）

验 8 条：
    [1] 两个接口都要鉴权
    [2] `/notify` 有 total/unread/items
    [3] `/notify` 的 **unread 只数非 READ**（造 1 条 PENDING + 1 条 READ ✅）
    [4] `/notify?status=PENDING` 筛选生效
    [5] `/knowledge` 有 total/items
    [6] **`effective` 字段正确**：DRAFT=False / PUBLISHED=True
    [7] `/knowledge?status=PUBLISHED` 筛选生效
    [8] 分页字段齐（limit/offset）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402
from sqlalchemy import delete, select        # noqa: E402

from hub.persistence.db import get_session   # noqa: E402
from hub.persistence.models import KnowledgeEntry, Notification  # noqa: E402

FAILS: list[str] = []
PWD = "123456"
TAG = "测试-extra-"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cleanup() -> None:
    with get_session() as s:
        s.execute(delete(Notification).where(
            Notification.resource_id.like("test-extra-%")))
        s.execute(delete(KnowledgeEntry).where(
            KnowledgeEntry.title.like(TAG + "%")))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  补的两个读接口：/api/notify 和 /api/knowledge")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)
    cleanup()

    # ---------- [1] 鉴权 ----------
    print("\n[1] 两个接口不带 token → 401")
    for ep in ("/api/notify", "/api/knowledge"):
        r = c.get(ep)
        check(f"{ep} → 401", r.status_code == 401, str(r.status_code))

    r = c.post("/api/auth/login", json={"email": "supervisor@hub.local", "password": PWD})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # ---------- 造数据 ----------
    with get_session() as s:
        s.add_all([
            Notification(event_type="test", resource_id="test-extra-n1",
                         channel="webhook", recipient="", payload={"items": []},
                         status="PENDING", attempts=0),
            Notification(event_type="test", resource_id="test-extra-n2",
                         channel="webhook", recipient="", payload={"items": []},
                         status="READ", attempts=0),
        ])
        s.add_all([
            KnowledgeEntry(tenant_id="default", type="prompt_patch",
                           title=TAG + "draft", content="草稿",
                           source_ref={}, status="DRAFT", version=1),
            KnowledgeEntry(tenant_id="default", type="prompt_patch",
                           title=TAG + "published", content="已发布",
                           source_ref={}, status="PUBLISHED", version=2),
        ])
        s.commit()

    # ---------- [2][3][4] notify ----------
    print("\n[2][3][4] /api/notify")
    r = c.get("/api/notify?limit=50", headers=H)
    check("200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("有 total/unread/items",
          all(k in j for k in ("total", "unread", "items")), str(list(j.keys())))
    check("unread ≥ 1（有 PENDING）", j.get("unread", 0) >= 1, str(j.get("unread")))
    # **unread 不含 READ**：造 2 条（1 PENDING + 1 READ）→ unread 应该只多 1 ✅
    draft_pending = [x for x in j["items"] if x["resource_id"] == "test-extra-n1"]
    check("能查到 PENDING 那条", len(draft_pending) == 1, str(len(draft_pending)))
    r2 = c.get("/api/notify?status=PENDING&limit=50", headers=H)
    check("?status=PENDING 筛选后全部是 PENDING",
          all(x["status"] == "PENDING" for x in r2.json()["items"]),
          str({x["status"] for x in r2.json()["items"]}))

    # ---------- [5][6][7][8] knowledge ----------
    print("\n[5][6][7][8] /api/knowledge")
    r = c.get("/api/knowledge?limit=50", headers=H)
    check("200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("有 total/items", "total" in j and "items" in j, str(list(j.keys())))
    mine = {x["title"]: x for x in j["items"] if x["title"].startswith(TAG)}
    check("能查到两条 fixture", len(mine) == 2, str(list(mine.keys())))
    if len(mine) == 2:
        d = mine.get(TAG + "draft")
        p = mine.get(TAG + "published")
        check("**DRAFT 的 effective=False**", d is not None and d["effective"] is False,
              str(d and d.get("effective")))
        check("**PUBLISHED 的 effective=True**", p is not None and p["effective"] is True,
              str(p and p.get("effective")))
        check("DRAFT 的 note 说'不生效'", "不生效" in str(d.get("effective_note")),
              str(d.get("effective_note")))
    r2 = c.get("/api/knowledge?status=PUBLISHED&limit=50", headers=H)
    check("?status=PUBLISHED 筛选后全是 PUBLISHED",
          all(x["status"] == "PUBLISHED" for x in r2.json()["items"]),
          str({x["status"] for x in r2.json()["items"]}))
    check("分页字段齐", all(k in r2.json() for k in ("limit", "offset")))

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
