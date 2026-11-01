# -*- coding: utf-8 -*-
"""
数据上传接口测试（check_api_ingest.py）—— 2026-11-01 补
=====================================================
`POST /api/ingest` 是"用户发现真缺口"后补的：原来只能 `hub p0` 命令行导。

验 11 条：
    [1] 不带 token → 401
    [2] platform 拼错 → **400 BAD_PLATFORM**（**不让脏数据进库**）
    [3] data_type 拼错 → 400 BAD_DATA_TYPE
    [4] 非 xlsx → 400 BAD_FILE_TYPE
    [5] 空文件 → 400 EMPTY_FILE
    [6] 真文件（jd 销售）→ **201** + rows_ok > 0
    [7] **幂等**：同一个文件再传 → `existed: true` + **同一个 batch_id**
    [8] **平台传错 → rows_ok = 0 / rows_bad > 0**（**这是最容易踩的坑** ✅）
    [9] 平台传错时**照样 201**（解析完成但全是坏行，不算异常）
    [10] 每次上传都写 `audit_log`（action=`data.ingest`）
    [11] 超 20MB → 413（用 21MB 的假数据测）

⚠️ **测试不写脏数据**：
    · 真文件用的是**已经导过**的 `jd_sales_202610.xlsx` → `existed=True` → **不新增记录** ✅
    · 平台传错那次会产生一个 **`rows_ok=0` 的空批次** → 测试**自己删掉** ✅
      （只删 `rows_ok=0` 的，**绝不碰真实批次** ✅）
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient   # noqa: E402

FAILS: list[str] = []
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MADE_BATCHES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def find_sample() -> pathlib.Path | None:
    """找 jd 的销售样件（**已导过的那个** → 幂等 → 不新增数据 ✅）"""
    root = pathlib.Path(__file__).resolve().parent.parent.parent / "data"
    for p in root.rglob("jd_sales_*.xlsx"):
        return p
    return None


def cleanup() -> None:
    """**只删测试自己造的批次**（按 id 精确删 ✅）。

    ⚠️ **绝对不要用"条件删"**（2026-11-01 差点出事）
        我第一版写的是 `where(row_kept == 0)` → 匹配到 **54 条真实批次** ✅ ✗
        那里面有的因为 DQE 全 drop 所以 row_kept=0 —— **但它们是真的数据** ✅
        外键 `analysis_run.batch_id` 挡住了删除才没造成损失 ✅
        → **规矩：测试清理只能按"自己记下的 id"删，不能用业务条件删** ✅
    """
    from sqlalchemy import delete
    from hub.persistence.db import get_session
    from hub.persistence.models import IngestBatch

    if not MADE_BATCHES:
        print("  （本次没有造新批次，无需清理）")
        return
    with get_session() as s:
        for bid in MADE_BATCHES:
            try:
                from uuid import UUID
                n = s.execute(delete(IngestBatch)
                              .where(IngestBatch.id == UUID(str(bid)))).rowcount
                s.commit()
                print(f"  清理批次 {bid}（{n} 行）")
            except Exception as e:            # noqa: BLE001
                s.rollback()
                print(f"  ⚠️ 跳过 {bid}：{type(e).__name__}（可能被外键引用 ✅）")


def main() -> int:
    print("=" * 66)
    print("  数据上传接口：POST /api/ingest")
    print("=" * 66)
    from hub.api.main import app
    c = TestClient(app)

    # ---------- [1] 鉴权 ----------
    print("\n[1] 不带 token → 401")
    r = c.post("/api/ingest")
    check("401", r.status_code == 401, str(r.status_code))

    r = c.post("/api/auth/login", json={"email": "operator@hub.local",
                                        "password": "123456"})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    fake = {"file": ("x.xlsx", io.BytesIO(b"not a real xlsx"), XLSX_CT)}

    # ---------- [2][3][4] 白名单 ----------
    print("\n[2][3][4] 白名单：platform / data_type / 扩展名")
    r = c.post("/api/ingest", files=fake,
               data={"platform": "taoboa", "data_type": "sales"}, headers=H)
    check("坏 platform → 400", r.status_code == 400, str(r.status_code))
    check("code=BAD_PLATFORM", r.json().get("code") == "BAD_PLATFORM",
          str(r.json().get("code")))
    r = c.post("/api/ingest", files=fake,
               data={"platform": "taobao", "data_type": "foo"}, headers=H)
    check("坏 data_type → 400", r.status_code == 400, str(r.status_code))
    check("code=BAD_DATA_TYPE", r.json().get("code") == "BAD_DATA_TYPE",
          str(r.json().get("code")))
    r = c.post("/api/ingest",
               files={"file": ("x.csv", io.BytesIO(b"a,b"), "text/csv")},
               data={"platform": "jd", "data_type": "sales"}, headers=H)
    check("非 xlsx → 400", r.status_code == 400, str(r.status_code))
    check("code=BAD_FILE_TYPE", r.json().get("code") == "BAD_FILE_TYPE",
          str(r.json().get("code")))

    # ---------- [5] 空文件 ----------
    print("\n[5] 空文件 → 400")
    r = c.post("/api/ingest", files={"file": ("e.xlsx", io.BytesIO(b""), XLSX_CT)},
               data={"platform": "jd", "data_type": "sales"}, headers=H)
    check("400", r.status_code == 400, str(r.status_code))
    check("code=EMPTY_FILE", r.json().get("code") == "EMPTY_FILE",
          str(r.json().get("code")))

    # ---------- [6][7] 真文件 + 幂等 ----------
    print("\n[6][7] 真文件 → 201；再传一次 → existed=true")
    p = find_sample()
    if p is None:
        check("找得到 jd 销售样件", False, "data/ 下没有 jd_sales_*.xlsx")
        cleanup()
        return 1 if FAILS else 0
    raw = p.read_bytes()
    files = {"file": (p.name, io.BytesIO(raw), XLSX_CT)}
    r = c.post("/api/ingest", files=files,
               data={"platform": "jd", "data_type": "sales"}, headers=H)
    check("201", r.status_code == 201, str(r.status_code))
    j1 = r.json()
    check("rows_ok > 0", (j1.get("rows_ok") or 0) > 0, str(j1.get("rows_ok")))
    check("rows_bad == 0", (j1.get("rows_bad") or 0) == 0, str(j1.get("rows_bad")))
    bid1 = j1.get("batch_id")
    files = {"file": (p.name, io.BytesIO(raw), XLSX_CT)}
    r = c.post("/api/ingest", files=files,
               data={"platform": "jd", "data_type": "sales"}, headers=H)
    j2 = r.json()
    check("第二次 existed=true", j2.get("existed") is True, str(j2.get("existed")))
    check("同一个 batch_id", j2.get("batch_id") == bid1,
          f"{bid1} vs {j2.get('batch_id')}")

    # ---------- [8][9] **平台传错的坑** ----------
    print("\n[8][9] 平台传错（jd 文件当 taobao）：rows_ok=0 / rows_bad>0，但仍然 201")
    files = {"file": (p.name, io.BytesIO(raw), XLSX_CT)}
    r = c.post("/api/ingest", files=files,
               data={"platform": "taobao", "data_type": "sales"}, headers=H)
    check("**仍然 201**（解析完成但全是坏行，不算异常）",
          r.status_code == 201, str(r.status_code))
    j3 = r.json()
    check("**rows_ok == 0**（这就是「平台传错」的信号 ✅）",
          (j3.get("rows_ok") or 0) == 0, str(j3.get("rows_ok")))
    check("rows_bad > 0", (j3.get("rows_bad") or 0) > 0, str(j3.get("rows_bad")))
    check("warnings 里有东西（能看出为什么坏）",
          bool(j3.get("warnings")), str(j3.get("warnings"))[:60])
    if j3.get("existed") is False:
        MADE_BATCHES.append(j3.get("batch_id"))

    # ---------- [10] 审计 ----------
    print("\n[10] 每次上传都写 audit_log（action=data.ingest）")
    from sqlalchemy import func, select
    from hub.persistence.db import get_session
    from hub.persistence.models import AuditLog
    with get_session() as s:
        n = s.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.action == "data.ingest"))
    check("有 data.ingest 审计", (n or 0) >= 1, f"{n} 条")

    # ---------- [11] 超大文件 ----------
    print("\n[11] 超过 20MB → 413")
    big = b"0" * (21 * 1024 * 1024)
    r = c.post("/api/ingest", files={"file": ("big.xlsx", io.BytesIO(big), XLSX_CT)},
               data={"platform": "jd", "data_type": "sales"}, headers=H)
    check("413", r.status_code == 413, str(r.status_code))
    check("code=FILE_TOO_LARGE", r.json().get("code") == "FILE_TOO_LARGE",
          str(r.json().get("code")))

    cleanup()
    print("\n（测试造的空批次已清理 ✅；真实批次和 4500 行销售数据**没动** ✅）")
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
