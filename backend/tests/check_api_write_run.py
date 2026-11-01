# -*- coding: utf-8 -*-
"""
写接口 5.1 测试（check_api_write_run.py）
========================================
验 10 条：
    [1] 三个写接口都要鉴权（不带 token → 401）
    [2] `POST /api/runs` → **202**，返回 run_id
    [3] 重复提交 → **409 + 已有 run_id**（不是 500）
    [4] `GET /api/runs/{id}` → 有 status/current_stage/kind
    [5] 坏 id → 400；不存在 → 404
    [6] `POST /api/handle` → 202，run_id + cluster_id
    [7] **同簇重复提交 → 409 + 同一个 run_id**（幂等 ✅）
    [8] 簇不存在 → 404
    [9] 前缀歧义 → 400（或正常解析，取决于数据）
    [10] 队列里的任务**真的能在 DB 查到**（不是只在 Redis ✅）

⚠️ **不真跑 worker**（那要 40 分钟 ✅）→ 只验"入队 + 状态可查" ✅
   `POST /api/runs` 会真入队一条 full 链 —— 测完**手动清掉**（不然会占着 UNIQUE ✅）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
PWD = "123456"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cleanup() -> None:
    """清掉测试造的 QUEUED 任务（不然占住 UNIQUE(kind) ✅）。"""
    from sqlalchemy import delete
    from hub.persistence.db import get_session
    from hub.persistence.models import PipelineRun
    with get_session() as s:
        s.execute(delete(PipelineRun).where(
            PipelineRun.status == "QUEUED",
            PipelineRun.kind.in_(("full",)) |
            PipelineRun.kind.like("handle:%")))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  写接口 5.1：提交与查询")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)

    cleanup()

    # ---------- [1] 鉴权 ----------
    print("\n[1] 三个写接口不带 token 全部 401")
    for ep, method in (("/api/runs", "post"), ("/api/handle", "post"),
                       ("/api/runs/00000000-0000-0000-0000-000000000000", "get")):
        r = getattr(c, method)(ep)
        check(f"{method.upper()} {ep} → 401", r.status_code == 401, str(r.status_code))

    r = c.post("/api/auth/login", json={"email": "operator@hub.local", "password": PWD})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # ---------- [2][3] POST /runs ----------
    print("\n[2][3] POST /api/runs → 202；重复提交 → 409")
    r = c.post("/api/runs", json={"kind": "full"}, headers=H)
    ok2 = r.status_code == 202
    check("202", ok2, str(r.status_code))
    run_id = (r.json() or {}).get("run_id") if ok2 else None
    check("有 run_id", bool(run_id), str(run_id))
    r2 = c.post("/api/runs", json={"kind": "full"}, headers=H)
    check("重复提交 → 409", r2.status_code == 409, str(r2.status_code))
    check("409 里带已有 run_id", (r2.json() or {}).get("run_id") == run_id,
          str((r2.json() or {}).get("run_id")))

    # ---------- [4][5] GET /runs/{id} ----------
    print("\n[4][5] GET /api/runs/{id}")
    if run_id:
        r = c.get(f"/api/runs/{run_id}", headers=H)
        check("200", r.status_code == 200, str(r.status_code))
        j = r.json()
        check("有 status/kind/current_stage",
              all(k in j for k in ("status", "kind", "current_stage")), str(list(j.keys())))
        check("status=QUEUED", j.get("status") == "QUEUED", str(j.get("status")))
    r = c.get("/api/runs/not-a-uuid", headers=H)
    check("坏 id → 400", r.status_code == 400, str(r.status_code))
    r = c.get("/api/runs/00000000-0000-0000-0000-000000000000", headers=H)
    check("不存在 → 404", r.status_code == 404, str(r.status_code))

    # ---------- [6][7][8] POST /handle ----------
    print("\n[6][7][8] POST /api/handle → 202；同簇重复 → 409")
    from sqlalchemy import select
    from hub.persistence.db import get_session
    from hub.persistence.models import IssueCluster
    with get_session() as s:
        cl = s.scalars(select(IssueCluster).limit(1)).first()
        cid = str(cl.id) if cl else None
    if cid is None:
        check("库里有簇可测", False)
    else:
        r = c.post("/api/handle", json={"cluster_id": cid}, headers=H)
        check("202", r.status_code == 202, str(r.status_code))
        h_run = (r.json() or {}).get("run_id")
        check("有 run_id", bool(h_run), str(h_run))
        check("回显 cluster_id", (r.json() or {}).get("cluster_id") == cid)
        # 同簇再来一次 → 409 + 同一个 id ✅
        r2 = c.post("/api/handle", json={"cluster_id": cid}, headers=H)
        check("同簇重复 → 409", r2.status_code == 409, str(r2.status_code))
        check("409 里带同一个 run_id", (r2.json() or {}).get("run_id") == h_run,
              str((r2.json() or {}).get("run_id")))
        # 前缀也能解析 ✅
        r3 = c.post("/api/handle", json={"cluster_id": cid[:8]}, headers=H)
        check("前 8 位前缀也能用（会 409 因为同簇 ✅）",
              r3.status_code in (202, 409), str(r3.status_code))
        # 不存在的簇 → 404 ✅
        r4 = c.post("/api/handle", json={"cluster_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
                    headers=H)
        check("不存在的簇 → 404", r4.status_code == 404, str(r4.status_code))

    # ---------- [10] 队列里的任务在 DB 里查得到 ----------
    print("\n[10] 入队的任务**在 DB 里查得到**（不是只在 Redis）")
    from hub.persistence.models import PipelineRun
    with get_session() as s:
        n = s.query(PipelineRun).filter(PipelineRun.status == "QUEUED").count()
    check("DB 里有 QUEUED 任务", n >= 1, f"{n} 条")

    cleanup()
    print("\n（QUEUED 任务已清理 ✅）")
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
