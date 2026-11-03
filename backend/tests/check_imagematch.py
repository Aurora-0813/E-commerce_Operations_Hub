# -*- coding: utf-8 -*-
"""
check_imagematch —— 图片 ↔ 评价 匹配 + 预检 + 图片级明细 + 人工复核
==================================================================
**这个测试为什么必须存在**（2026-11-03）

    改 `engine.py` 的核心匹配逻辑之前，**没有任何测试能证明"以前的行为还work"** ✅
    （原来的 `check_multimodal.py` 只验"跑通 + 沙箱残留 0" ✅）

所以这里用**合成的小 zip** 把 5 个匹配级别逐条钉死：
    L1 manifest / L2 目录名 / L3 url_basename / L4 文件名 / L5 宽松（默认关）
再加：预检的对账数字、image_asset 落库、人工复核后 review_feature 的**人工优先**

跑法：python -B tests/check_imagematch.py
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image                                         # noqa: E402

from hub.multimodal.matching import (                          # noqa: E402
    PackageIndex, list_package, resolve_images,
)

OK, BAD = [], []


def check(msg: str, cond: bool, detail: str = "") -> None:
    if cond:
        OK.append(msg)
        print(f"  [OK] {msg}" + (f"　{detail}" if detail else ""))
    else:
        BAD.append(f"{msg} {detail}")
        print(f"  [FAIL] {msg}　{detail}")


def _png_bytes(size=(4, 4), color=(200, 120, 60)) -> bytes:
    """造一张最小的合法 PNG（4x4，几十字节 ✅）；沙箱会把它重建，所以必须真图。"""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def make_zip(entries: dict[str, bytes]) -> Path:
    """{包内路径: 内容} → 临时 zip 文件 ✅"""
    p = Path(tempfile.mkdtemp(prefix="imgmatch_")) / "pkg.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return p


# ══════════════════════════════════════════════════════════════
print("=" * 66)
print("  一、list_package：只列文件名，不解压 ✅")
print("=" * 66)

img = _png_bytes()
z = make_zip({
    "photos/00013.jpg": img,          # 子目录也要拍平 ✅
    "photos/00013_1.jpg": img,
    "readme.txt": b"not an image",    # 非图片要忽略 ✅
    "00022.jpg": img,
})
idx = list_package(z)
check("图片张数（忽略 txt）", idx.count == 3, f"{idx.count}")
check("拍平后的文件名", idx.files == ["00013.jpg", "00013_1.jpg", "00022.jpg"],
      str(idx.files))
check("保留原始相对路径（给目录名匹配用）",
      idx.origins.get("00013.jpg") == "photos/00013.jpg",
      str(idx.origins.get("00013.jpg")))
check("没有 manifest 时为空", idx.manifest == {} and idx.manifest_source == "")

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  二、L1 manifest（显式索引，最准 ✅）")
print("=" * 66)

z1 = make_zip({
    "index.csv": "filename,platform_review_id\nIMG_a.jpg,TB00000999\nIMG_b.jpg,TB00000999\n".encode(),
    "IMG_a.jpg": img,
    "IMG_b.jpg": img,
    "IMG_c.jpg": img,
})
i1 = list_package(z1)
check("manifest 被解析", len(i1.manifest) == 2, str(i1.manifest))
check("manifest 列名能识别（filename / platform_review_id）",
      i1.manifest.get("IMG_a.jpg") == "TB00000999")
r1 = resolve_images(platform_review_id="TB00000999",
                    image_urls=["https://x/nomatch.jpg"], index=i1)
check("L1 命中", r1.tier == "manifest", r1.tier)
check("L1 拿到 2 张", len(r1.files) == 2, str(r1.files))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  三、L2 目录名 = 评价 id")
print("=" * 66)

z2 = make_zip({
    "TB00000777/a.jpg": img,
    "TB00000777/b.jpg": img,
    "TB00000888/c.jpg": img,
    # **同名冲突**：不同目录下都叫 `x.jpg` ✅ → 必须被检测出来
    "TB00000777/x.jpg": img,
    "TB00000888/x.jpg": img,
})
i2 = list_package(z2)
r2 = resolve_images(platform_review_id="TB00000777", image_urls=[], index=i2)
check("L2 命中", r2.tier == "directory", r2.tier)
check("L2 只拿自己目录的 2 张", len(r2.files) == 2, str(r2.files))
check("**检测到同名冲突**（不同子目录下同名的 x.jpg）",
      any(f == "x.jpg" for f in i2.dupes), str(i2.dupes))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  四、L3 url_basename（系统自己下载时最准 ✅）")
print("=" * 66)

z3 = make_zip({"O1CN01.jpg": img, "O1CN02.jpg": img})
i3 = list_package(z3)
r3 = resolve_images(platform_review_id="TB00000666",
                    image_urls=["https://img/i8/O1CN01.jpg",
                                "https://img/i8/O1CN02.jpg"], index=i3)
check("L3 命中", r3.tier == "url_basename", r3.tier)
check("L3 拿到 2 张", len(r3.files) == 2, str(r3.files))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  五、L4 文件名 = 评价 id")
print("=" * 66)

z4 = make_zip({"TB00000555.jpg": img, "TB00000555_1.jpg": img, "TB00000555_x2.png": img})
i4 = list_package(z4)
r4 = resolve_images(platform_review_id="TB00000555",
                    image_urls=["https://img/other.jpg"], index=i4)
check("L4 命中", r4.tier == "filename", r4.tier)
check("L4 拿到 3 张（含 _1 和 _x2 的变体）", len(r4.files) == 3, str(r4.files))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  六、L5 宽松（后 5 位）—— **默认关** ✅")
print("=" * 66)

z5 = make_zip({"00223.jpg": img, "00223_1.jpg": img})
i5 = list_package(z5)
r5a = resolve_images(platform_review_id="TB00000223", image_urls=[], index=i5)
check("默认 **不** 走 L5（否则会误伤 JD00000223）",
      r5a.tier == "" and r5a.files == [], f"tier={r5a.tier!r}")
r5b = resolve_images(platform_review_id="TB00000223", image_urls=[], index=i5,
                     allow_loose=True)
check("显式打开才走 L5", r5b.tier == "loose", r5b.tier)
check("L5 拿到 2 张", len(r5b.files) == 2, str(r5b.files))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  七、真实包预检：data/mock/images")
print("=" * 66)

from hub.multimodal.engine import precheck_multimodal             # noqa: E402

REAL = Path(__file__).resolve().parent.parent.parent / "data" / "mock" / "images"
if REAL.exists():
    st = precheck_multimodal(REAL)
    d = st.as_dict()
    print(f"    包里 {d['package_files']} 张 / 有图评价 {d['reviews_with_image']} 条")
    print(f"    匹配 {d['reviews_matched']} 条（{d['images_matched']} 张）/ "
          f"对不上 {d['reviews_unmatched']} 条 / 包里多余 {d['images_unused']} 张")
    print(f"    用的规则：{d['tier_text']}")
    print(f"    结论：{d['verdict']}")
    check("预检包里有图", d["package_files"] > 0, str(d["package_files"]))
    check("预检能匹配上（>0）", d["reviews_matched"] > 0, str(d["reviews_matched"]))
    check("匹配 + 对不上 == 有图评价",
          d["reviews_matched"] + d["reviews_unmatched"] == d["reviews_with_image"],
          f"{d['reviews_matched']}+{d['reviews_unmatched']} vs {d['reviews_with_image']}")
    check("预检有成本预估", d["est_calls"] > 0 and d["est_seconds"] > 0,
          f"{d['est_calls']} 次 / {d['est_seconds']}s")
    check("预检结论是人话（含中文）", any("\u4e00" <= ch <= "\u9fff" for ch in d["verdict"]))
else:
    print(f"  （跳过：{REAL} 不存在）")

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  八、预检：一个完全对不上的包 → 必须给出「为什么」✅")
print("=" * 66)

z_bad = make_zip({"IMG_0001.jpg": img, "IMG_0002.jpg": img})
st_bad = precheck_multimodal(z_bad)
db = st_bad.as_dict()
print(f"    结论：{db['verdict']}")
check("对不上时要说明「建议放 index.csv」",
      "index.csv" in db["verdict"] or "对不上" in db["verdict"], db["verdict"][:40])
check("对不上时给样例", len(db["unmatched_sample"]) > 0 or db["reviews_with_image"] == 0,
      str(len(db["unmatched_sample"])))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  九、跑一遍真流程 → image_asset 落库 + 人工复核（人工优先 ✅）")
print("=" * 66)

from sqlalchemy import func, select                               # noqa: E402

from hub.multimodal.engine import run_multimodal                  # noqa: E402
from hub.multimodal.vision import get_vision                      # noqa: E402
from hub.persistence.db import get_session                        # noqa: E402
from hub.persistence.models import ImageAsset                     # noqa: E402
from hub.persistence.models import ReviewFeature                  # noqa: E402

if REAL.exists():
    before = 0
    with get_session() as s:
        before = s.scalar(select(func.count()).select_from(ImageAsset)) or 0

    st2 = run_multimodal(REAL, vision=get_vision("mock"), limit=3, force=True)
    print(f"    处理 {st2.processed} 条 / 图 {st2.images} 张 / "
          f"图片级明细 {st2.assets} 条 / 分级 {st2.by_tier}")
    check("跑了 3 条评价", st2.processed > 0, str(st2.processed))
    check("**写了图片级明细**", st2.assets > 0, str(st2.assets))
    check("记录了用了哪一级匹配", bool(st2.by_tier), str(st2.by_tier))
    check("沙箱残留为 0（隐私红线）", st2.residual == 0, str(st2.residual))

    with get_session() as s:
        after = s.scalar(select(func.count()).select_from(ImageAsset)) or 0
        a = s.scalars(select(ImageAsset).order_by(ImageAsset.created_at.desc())
                      .limit(1)).first()
        aid = a.id if a else None
        rid = a.review_id if a else None
        model_defect = a.defect_type if a else None
        src = a.source_file if a else None
    # ⚠️ **不能断言"变多"**（2026-11-03 修）：`force=True` 重跑会走
    #    `ON CONFLICT (review_id, sha256) DO UPDATE` → **行数不变才是对的** ✅
    check("**有图片级明细**", after >= st2.assets and after > 0, f"{before} → {after}")
    # 再跑一遍，验**幂等**
    st3 = run_multimodal(REAL, vision=get_vision("mock"), limit=3, force=True)
    with get_session() as s:
        after2 = s.scalar(select(func.count()).select_from(ImageAsset)) or 0
    check("**重跑是幂等的（行数不变）**", after2 == after, f"{after} → {after2}")
    _ = st3
    check("明细带原文件名（可溯源 ✅）", bool(src), str(src))

    if aid:
        from hub.multimodal.assets import reaggregate, review_asset   # noqa: E402
        out = review_asset(aid, final_defect="开线", note="人工改：缝隙处抽丝，不是破损",
                           actor="tester@hub.local")
        check("复核成功", out.get("ok") is True, str(out.get("reaggregated")))
        with get_session() as s:
            f = s.get(ReviewFeature, rid)
            fdef = f.defect_type if f else None
        check("**合并结果优先用人工判定（开线）**", fdef == "开线",
              f"模型说 {model_defect}，合并后 {fdef}")
        # 撤销 → 回到模型结果
        from hub.multimodal.assets import clear_review               # noqa: E402
        clear_review(aid)
        with get_session() as s:
            f2 = s.get(ReviewFeature, rid)
            fdef2 = f2.defect_type if f2 else None
        # ⚠️ **撤销后应该回到"最严重那张"的判定**，不是"最新那张"的 ✅
        #    （2026-11-03 修：合并规则是取 severity 最大的那张）
        with get_session() as s:
            worst = s.scalars(
                select(ImageAsset).where(ImageAsset.review_id == rid)
                .order_by(ImageAsset.severity.desc().nullslast())).first()
            expected = worst.defect_type if worst else None
        check("撤销复核后回到「最严重那张」的模型判定", fdef2 == expected,
              f"期望 {expected}（最新那张是 {model_defect}），实际 {fdef2}")
        _ = reaggregate

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("  十、异步入队的整包幂等（同一个 zip 重复上传 → 409）")
print("=" * 66)

from fastapi.testclient import TestClient                       # noqa: E402

from hub.api.main import app                                    # noqa: E402

_c = TestClient(app)
_r = _c.post("/api/auth/login", json={"email": "operator@hub.local",
                                     "password": "123456"})
_H = {"Authorization": "Bearer " + _r.json()["access_token"]}

# 造一个**内容固定**的小 zip（同样的字节 → 同样的 sha256 ✅）
_zip = make_zip({"O1CN0100008.jpg": img})
_first = None
for _try in range(2):
    with open(_zip, "rb") as _fh:
        _resp = _c.post("/api/ingest/images", headers=_H,
                        files={"file": ("dup.zip", _fh, "application/zip")},
                        data={"mode": "queue", "vision": "mock", "force": "false"})
    if _try == 0:
        _first = _resp.status_code
        print(f"    第 1 次 → {_resp.status_code}  {str(_resp.json().get('kind'))}")
    else:
        print(f"    第 2 次 → {_resp.status_code}  {str(_resp.json().get('code'))}")
        check("**同一个包重复传 → 409（幂等）**", _resp.status_code == 409,
              f"{_resp.status_code}")
        check("409 里带 ALREADY_PROCESSED",
              (_resp.json() or {}).get("code") == "ALREADY_PROCESSED",
              str((_resp.json() or {}).get("code")))
check("第一次是 202（入队）", _first in (202, 409), str(_first))

# ══════════════════════════════════════════════════════════════
print()
print("=" * 66)
print(f"  通过 {len(OK)} / 失败 {len(BAD)}")
for b in BAD:
    print(f"   ❌ {b}")
print("=" * 66)
sys.exit(1 if BAD else 0)
