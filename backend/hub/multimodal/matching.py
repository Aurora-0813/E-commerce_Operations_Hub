# -*- coding: utf-8 -*-
"""
图片 ↔ 评价 的匹配（matching.py）—— 2026-11-03 新增
=====================================================
**为什么重写这一层**

原来的匹配只有一句：

    path = sb.find(Path(url).name)

也就是**只按 image_url 的文件名**去沙箱里找。问题（2026-11-02 实测）：

    · DB 里 image_url 的 basename 是 `O1CN0100223.jpg`
    · 而我们本地那套图片叫 `00013.jpg`
    · → **一张都对不上**，实测 232 条有图评价只匹配上 1 条
    · → 而且是**静默跳过**（`skipped 231`），用户不知道为什么

真实业务里图片来源有三种，只有第一种能靠 URL 文件名：

    ① 平台 API 批量拉   → 文件名是**我们自己起的**，能对上 ✅
    ② 商家/客服手动打包 → `IMG_20261001.jpg` / `截图(3).png`，**对不上** ✅
    ③ 数据团队按规范导出 → 带 `index.csv`，**最可靠** ✅

所以这里做两件事：

    **一、分级匹配**（按可靠度从高到低，命中即停，并记录"用了哪一级"）
        L1 manifest    zip 里带 index.csv/json（filename → review_id）  🟢 最准
        L2 url_basename image_url 的 basename 直接命中                  🟢 系统自己下载时最准
        L3 directory   目录名 = 评价 id（TB00000223/1.jpg）             🟡 人工按评价整理
        L4 filename    文件名 = 评价 id（TB00000223.jpg / _1.jpg）      🟡 部分商家
        L5 loose       评价 id 后 5 位 + 后缀（223.jpg）**默认关**      🔴 会误伤

    **二、预检（precheck）**
        只**列文件、不解压、不调模型** → 秒回"能对上多少 / 对不上多少 / 用哪一级"
        → 用户先看对账，再点"开始处理"，**不会白烧一小时模型时间**

**为什么 L5 默认关**
    `223.jpg` 这种后缀匹配会误伤：`TB00000223` 和 `JD00000223` 的后 5 位都是 `00223`。
    宽松规则必须**显式打开**，否则"匹配变多"这种好事会悄悄改变历史行为。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════

#: 沙箱/包里认的图片扩展名（和 sandbox.IMAGE_EXTS 保持一致 ✅）
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

#: manifest 文件名候选（zip 根目录下，大小写不敏感）
MANIFEST_NAMES = ("index.csv", "manifest.csv", "index.json", "manifest.json")

#: 匹配级别（有序：前面的优先 ✅）
TIERS = ("manifest", "url_basename", "directory", "filename", "loose")

#: 级别的人话（给前端/CLI 显示 ✅）
TIER_TEXT = {
    "manifest": "显式索引（index.csv / manifest.csv）",
    "url_basename": "评价里的图片 URL 文件名",
    "directory": "图片子目录名 = 评价 id",
    "filename": "图片文件名 = 评价 id",
    "loose": "评价 id 后 5 位（宽松规则，可能误伤）",
}


# ══════════════════════════════════════════════════════════════
#  图片包索引（**不解压**，只列文件名 ✅）
# ══════════════════════════════════════════════════════════════
@dataclass
class PackageIndex:
    """一个图片包的"目录清单"（不含像素数据 ✅）。

    :ivar files:   拍平后的文件名列表（沙箱也是拍平的，一一对应 ✅）
    :ivar origins: 文件名 → 原始相对路径（`TB00000223/1.jpg`），给 L3 目录名用 ✅
    :ivar manifest: 文件名 → 评价 id（L1 用；没 manifest 就是空 dict ✅）
    :ivar manifest_source: 用的哪个文件名（""=没有 ✅）
    """
    files: list[str] = field(default_factory=list)
    origins: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, str] = field(default_factory=dict)
    manifest_source: str = ""
    #: 拍平后**同名冲突**的文件（不同子目录下同名 ✅）→ 这些不能用于 L2 目录名匹配
    dupes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.files)

    def has(self, filename: str) -> bool:
        return filename in self.origins


def _put_origin(idx: PackageIndex, flat: str, origin: str) -> None:
    """记录「拍平名 → 原始路径」，**同名冲突要检测** ✅

    为什么必须处理（2026-11-03 实测）
        包里 `TB00000777/1.jpg` 和 `TB00000888/1.jpg` 拍平后**都叫 `1.jpg`** ✅
        → 用 dict 直接赋会**后一个覆盖前一个** ✅ ✗
        → 于是 L2（目录名匹配）拿到的图**缺一张**，而且**没人知道** ✅
        修法：冲突时把 origin 置空（标记"不可用"）+ 记进 `dupes` ✅
              → 预检会把冲突数报出来 ✅（**不静默**）
    """
    if not origin or origin == flat:      # 根目录下的文件没有冲突风险 ✅
        idx.origins.setdefault(flat, flat)
        return
    prev = idx.origins.get(flat)
    if prev in (None, flat):
        idx.origins[flat] = origin
    elif prev != origin:                  # **真冲突** ✅
        idx.origins[flat] = ""            # 置空 = 不可用于 L2 ✅
        if flat not in idx.dupes:
            idx.dupes.append(flat)


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def list_package(source: str | Path) -> PackageIndex:
    """**只列文件名**，不解压、不写盘 ✅

    zip 用 `namelist()`（读中央目录，几十 KB 的 IO ✅）
    目录用 `rglob`（本来就在盘上 ✅）
    """
    source = Path(source)
    idx = PackageIndex()

    if source.is_dir():
        for f in source.rglob("*"):
            if f.is_file() and _is_image(f.name):
                idx.files.append(f.name)
                idx.origins[f.name] = f.relative_to(source).as_posix()
        idx.files.sort()
        return idx

    if source.suffix.lower() != ".zip":
        raise ValueError(f"不支持的图片包：{source}（只支持目录或 zip）")

    with zipfile.ZipFile(source) as zf:
        for info in zf.infolist():
            if info.is_dir() or not _is_image(info.filename):
                continue
            flat = Path(info.filename).name
            idx.files.append(flat)
            _put_origin(idx, flat, info.filename)   # 保留 `a/b/1.jpg` 原样 ✅

        # 读 manifest（只读那一个小文件 ✅）
        for cand in MANIFEST_NAMES:
            hit = next((n for n in zf.namelist()
                        if Path(n).name.lower() == cand), None)
            if hit is None:
                continue
            try:
                raw = zf.read(hit).decode("utf-8-sig", errors="replace")
                idx.manifest = _parse_manifest(hit, raw)
                idx.manifest_source = hit
            except Exception as e:                  # noqa: BLE001
                # manifest 坏了**不能让整个预检失败** ✅ → 忽略它，降级匹配
                print(f"  [匹配] manifest 解析失败（{hit}）：{e}")
            break

    idx.files.sort()
    return idx


def _parse_manifest(name: str, raw: str) -> dict[str, str]:
    """解析 manifest → `{图片文件名: 评价 id}` ✅

    支持列名（大小写/空格不敏感 ✅）：
        filename / file / image / 文件名
        review_id / platform_review_id / id / 评价id / 评价编号
    """
    lower = name.lower()
    if lower.endswith(".json"):
        obj = json.loads(raw)
        # 两种形状都收：`[{...}]` 和 `{"items": [{...}]}` ✅
        rows = obj if isinstance(obj, list) else obj.get("items", [])
        out: dict[str, str] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            fn = _pick(r, ("filename", "file", "image", "文件名"))
            rid = _pick(r, ("review_id", "platform_review_id", "id", "评价id", "评价编号"))
            if fn and rid:
                out[str(fn)] = str(rid)
        return out

    # CSV
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        return {}
    out2: dict[str, str] = {}
    for r in reader:
        fn = _pick(r, ("filename", "file", "image", "文件名"))
        rid = _pick(r, ("review_id", "platform_review_id", "id", "评价id", "评价编号"))
        if fn and rid:
            out2[str(fn)] = str(rid)
    return out2


def _pick(row: dict, names: tuple[str, ...]) -> str | None:
    """从一行里按候选列名取值（大小写/空格不敏感 ✅）。"""
    norm = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for n in names:
        v = norm.get(n.lower())
        if v not in (None, ""):
            return str(v).strip()
    return None


# ══════════════════════════════════════════════════════════════
#  分级匹配
# ══════════════════════════════════════════════════════════════
@dataclass
class Resolved:
    """一条评价的匹配结果。"""
    tier: str = ""                     # 用了哪一级（""=没匹配上 ✅）
    files: list[str] = field(default_factory=list)   # 命中的沙箱文件名
    tried: list[str] = field(default_factory=list)   # 试过哪些候选（对账要展示 ✅）


def resolve_images(
    *,
    platform_review_id: str,
    image_urls: list[str] | None,
    index: PackageIndex,
    allow_loose: bool = False,
    max_per_review: int = 9,
) -> Resolved:
    """按**可靠度从高到低**给一条评价找图，**命中即停** ✅

    :param platform_review_id: 评价的平台 id（`TB00000013`）
    :param image_urls:         评价里的图片 URL 列表
    :param index:              `list_package()` 的结果
    :param allow_loose:        **是否允许 L5 宽松匹配**（默认 False ✅）
    """
    rid = str(platform_review_id or "").strip()
    urls = list(image_urls or [])
    res = Resolved()

    if index.count == 0:
        return res

    # ---------- L1：manifest（显式索引，最准 ✅）----------
    if index.manifest:
        hits = [fn for fn, r in index.manifest.items() if str(r) == rid and index.has(fn)]
        if hits:
            res.tier = "manifest"
            res.files = hits[:max_per_review]
            return res

    # ---------- L2：image_url 的 basename（系统自己下载时最准 ✅）----------
    hits2 = [Path(u).name for u in urls if index.has(Path(u).name)]
    if hits2:
        res.tier = "url_basename"
        res.files = hits2[:max_per_review]
        return res
    res.tried.extend(Path(u).name for u in urls)

    # ---------- L3：子目录名 = 评价 id ----------
    if rid:
        prefix = f"{rid}/"
        hits3 = [fn for fn, origin in index.origins.items()
                 if origin and origin.startswith(prefix)]
        if hits3:
            res.tier = "directory"
            res.files = hits3[:max_per_review]
            return res
        res.tried.append(f"{prefix}*")

    # ---------- L4：文件名 = 评价 id（`TB13.jpg` / `TB13_1.jpg`）----------
    if rid:
        stems = {Path(fn).stem for fn in index.files}
        hits4 = [fn for fn in index.files
                 if Path(fn).stem == rid or Path(fn).stem.startswith(f"{rid}_")]
        if hits4:
            res.tier = "filename"
            res.files = hits4[:max_per_review]
            return res
        res.tried.append(f"{rid}.jpg")
        _ = stems   # （保留：以后要做"近似文件名"提示时用 ✅）

    # ---------- L5：宽松（后 5 位后缀，**默认关** ✅）----------
    if allow_loose and len(rid) >= 5:
        tail = rid[-5:]
        hits5 = [fn for fn in index.files if Path(fn).stem.split("_")[0] == tail]
        if hits5:
            res.tier = "loose"
            res.files = hits5[:max_per_review]
            return res
        res.tried.append(f"{tail}.jpg")

    return res


# ══════════════════════════════════════════════════════════════
#  预检（**只对账，不调模型** ✅）
# ══════════════════════════════════════════════════════════════
@dataclass
class PrecheckStats:
    """一次预检的结果（全是计数，没有像素数据 ✅）。"""
    # 图片包侧
    package_files: int = 0                    # 包里的图片张数
    manifest_source: str = ""                 # 用的哪个 manifest 文件（""=没有）
    manifest_rows: int = 0                    # manifest 里有多少行
    name_collisions: int = 0                  # 拍平后同名冲突的张数（这些不能用于目录匹配 ✅）
    #: 整个 zip 的 sha256（**同一个包重复上传靠它判重** ✅）
    zip_sha256: str = ""
    #: 这个包之前处理过吗（查 `analysis_run` 里 M2 的历史 ✅）
    already_processed: bool = False
    #: 上次处理的 trace_id（能点开看历史 ✅）
    prev_trace_id: str = ""

    # 评价侧
    reviews_with_image: int = 0               # DB 里"有图"的评价数
    reviews_matched: int = 0                  # 能匹配上图的评价数
    reviews_unmatched: int = 0                # 一条都对不上的评价数
    images_matched: int = 0                   # 总共匹配上的图片张数
    images_unused: int = 0                    # 包里有、但没被任何评价用到的张数

    # 分级统计
    by_tier: dict[str, int] = field(default_factory=dict)   # 各级命中多少条评价
    tier_text: str = ""                       # 人话：主要用了哪一级

    # 样例（**给出证据，用户才能自己判断** ✅）
    unmatched_sample: list[dict] = field(default_factory=list)
    unused_sample: list[str] = field(default_factory=list)
    missing_sample: list[dict] = field(default_factory=list)  # 评价要几张、只找到几张

    # 成本预估
    est_seconds: float = 0.0                  # 预计耗时（本地 8.9 秒/张）
    est_calls: int = 0                        # 预计调模型次数（**没算 pHash 去重**）

    def as_dict(self) -> dict:
        return {
            "package_files": self.package_files,
            "manifest_source": self.manifest_source,
            "manifest_rows": self.manifest_rows,
            "name_collisions": self.name_collisions,
            "zip_sha256": self.zip_sha256,
            "already_processed": self.already_processed,
            "prev_trace_id": self.prev_trace_id,
            "reviews_with_image": self.reviews_with_image,
            "reviews_matched": self.reviews_matched,
            "reviews_unmatched": self.reviews_unmatched,
            "images_matched": self.images_matched,
            "images_unused": self.images_unused,
            "by_tier": self.by_tier,
            "tier_text": self.tier_text,
            "unmatched_sample": self.unmatched_sample,
            "unused_sample": self.unused_sample,
            "missing_sample": self.missing_sample,
            "est_calls": self.est_calls,
            "est_seconds": round(self.est_seconds, 1),
            # **一句话结论**（前端直接显示 ✅）
            "verdict": _verdict(self),
        }


def _verdict(st: PrecheckStats) -> str:
    """把对账结果总结成一句人话（**用户第一眼看的就是这句** ✅）。"""
    if st.reviews_with_image == 0:
        return "库里还没有「有图」的评价 —— 先导入评价数据（带图片 URL 的那种）"
    if st.reviews_matched == 0:
        return (f"**一张都对不上** —— {st.reviews_with_image} 条评价要图，"
                f"包里 {st.package_files} 张，但命名规则不一致。"
                "建议在 zip 里放一份 index.csv（列：filename,platform_review_id）")
    if st.reviews_unmatched > 0:
        return (f"部分对上：{st.reviews_matched} 条能匹配（{st.images_matched} 张），"
                f"还有 {st.reviews_unmatched} 条对不上 —— 下面有样例")
    return (f"全部对上：{st.reviews_matched} 条评价 / {st.images_matched} 张图"
            f"（预计调模型 {st.est_calls} 次，约 {st.est_seconds / 60:.1f} 分钟）")
