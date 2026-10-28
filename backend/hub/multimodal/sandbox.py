# -*- coding: utf-8 -*-
"""
图片沙箱（sandbox.py）
=====================
M2 的核心：**图片可以有，但必须"阅后即焚"**。

四件事：
    1. 把图片包（目录 / zip）复制到沙箱目录（优先 tmpfs 内存盘）
    2. **剥离元数据**（像素级重建，GPS/设备信息不留）
    3. 处理过程中图片只存在于沙箱
    4. 处理完**覆写销毁**（不是删除，是先用随机字节覆盖再删，防恢复）

为什么用 tmpfs：
    tmpfs 是内存盘，断电即失，不落物理磁盘。
    Linux 上 /dev/shm 就是 tmpfs；Windows 没有，退化为系统临时目录。

为什么剥离要用"像素级重建"而不是"删 EXIF"：
    删 EXIF 是黑名单——JPEG 在 APP1、PNG 在 tEXt、WebP 在 EXIF chunk，
    而且 EXIF 内嵌的缩略图可能单独带 GPS。黑名单必然有遗漏。
    像素级重建是白名单——只保留像素，元数据物理上不存在。
    allow-list 永远比 deny-list 可靠。
"""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

# 图片扩展名（沙箱里只认这些）
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 重编码质量（JPEG 用；85 肉眼几乎无损，但体积小很多）
JPEG_QUALITY = 85


def _shred_file(path: Path) -> None:
    """覆写销毁单个文件：随机字节覆盖 + fsync + unlink。

    为什么不是直接 unlink：
        普通删除只删"目录项"，磁盘上的数据块还在，可能被恢复。
        覆写一遍再删，才叫"销毁"。
    """
    if not path.exists() or not path.is_file():
        return
    size = path.stat().st_size
    with path.open("r+b") as f:
        f.write(os.urandom(max(size, 1)))
        f.flush()
        os.fsync(f.fileno())
    path.unlink()


def _default_base() -> Path:
    """沙箱根目录：优先 /dev/shm（内存盘），否则系统临时目录。"""
    shm = Path("/dev/shm")
    if shm.exists() and os.access(shm, os.W_OK):
        return shm
    return Path(tempfile.gettempdir())


class Sandbox:
    """图片沙箱（上下文管理器）。

    用法：
        with Sandbox() as sb:
            sb.ingest("images.zip")
            for img in sb.images():
                ...处理...
        # 退出 with 时自动覆写销毁所有图片
    """

    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else _default_base()
        self.root = Path(tempfile.mkdtemp(prefix="mm_sandbox_", dir=self.base))
        self.destroyed = 0
        self.stripped = 0        # 成功剥离元数据的张数

    # ---------- 上下文管理 ----------
    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # 无论成功失败，退出时必须销毁
        self.destroy_all()

    # ---------- 导入 ----------
    def ingest(self, source: str | Path) -> int:
        """把图片包导入沙箱，返回导入的图片张数。

        支持两种来源（对应真实业务）：
            - 目录：复制每个文件（开发/测试用）
            - zip ：解压（模拟商家上传的压缩包）
        """
        source = Path(source)
        if source.is_dir():
            for f in source.iterdir():
                if f.is_file():
                    shutil.copy2(f, self.root / f.name)
        elif source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as zf:
                zf.extractall(self.root)
        else:
            raise ValueError(f"不支持的图片包：{source}（只支持目录或 zip）")

        # zip 可能带子目录，把图片统一拍平到根目录，方便按文件名查找
        self._flatten()

        # 关键：在任何下游处理（pHash / Vision / 日志）之前剥离元数据。
        # 照片一进沙箱就"只剩像素"，后面的所有环节都不可能拿到 GPS。
        self.stripped = self._strip_metadata()

        return len(self.images())

    def _flatten(self) -> None:
        """把子目录里的图片移到沙箱根目录。"""
        for f in list(self.root.rglob("*")):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS and f.parent != self.root:
                target = self.root / f.name
                if not target.exists():
                    shutil.move(str(f), str(target))

    # ---------- 查询 ----------
    def images(self) -> list[Path]:
        """沙箱里的图片文件列表。"""
        return sorted(
            p for p in self.root.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )

    def find(self, filename: str) -> Path | None:
        """按文件名找图片（filename 来自评价里的 image_url 的 basename）。"""
        p = self.root / filename
        return p if p.exists() else None

    # ---------- 元数据剥离 ----------
    def _strip_one(self, path: Path) -> None:
        """像素级重建一张图，就地替换（文件名不变）。

        为什么是"重建"而不是"删标签"：
            `Image.frombytes` 创建**全新对象**，`.info` 为空 —— 元数据物理上不存在。
            而 `img.save(..., exif=b"")` 只管 JPEG 的 EXIF，
            还有 XMP / IPTC / ICC / 内嵌缩略图 / PNG 的 tEXt 块，删不干净。
        """
        with Image.open(path) as im:
            rgb = im.convert("RGB")            # 丢掉 alpha / 调色板 / ICC
            size = rgb.size
            pixels = rgb.tobytes()

        clean = Image.frombytes("RGB", size, pixels)   # ← 全新对象，零元数据

        # 尽量保持原格式（文件名不能变，下游靠文件名匹配 URL）
        ext = path.suffix.lower().lstrip(".")
        if ext in ("jpg", "jpeg"):
            fmt, kwargs = "JPEG", {"quality": JPEG_QUALITY}
        elif ext == "webp":
            fmt, kwargs = "WEBP", {"quality": JPEG_QUALITY}
        else:                                   # png / gif / bmp → 统一存 PNG
            fmt, kwargs = "PNG", {}

        tmp = path.with_name(path.name + ".stripped")
        clean.save(tmp, fmt, **kwargs)
        _shred_file(path)                       # 带 EXIF 的原文件覆写销毁
        tmp.replace(path)                       # 干净版顶替原文件名

    def _strip_metadata(self) -> int:
        """逐张剥离元数据，返回成功张数。

        失败处理：**销毁并跳过**。
        宁可少处理一张，也不能让带隐私的图进下游。
        """
        count = 0
        for p in self.images():
            try:
                self._strip_one(p)
                count += 1
            except Exception as e:              # noqa: BLE001
                print(f"  [沙箱] 剥离失败，销毁并跳过 {p.name}：{e}")
                _shred_file(p)
        return count

    # ---------- 销毁 ----------
    def destroy(self, path: Path) -> None:
        """覆写销毁单个文件：先用随机字节覆盖，再删除。

        为什么不是直接 unlink：
            普通删除只删"目录项"，磁盘上的数据块还在，可能被恢复。
            覆写一遍再删，才叫"销毁"。
        """
        if not path.exists() or not path.is_file():
            return
        _shred_file(path)
        self.destroyed += 1

    def destroy_all(self) -> None:
        """销毁沙箱里所有文件，并删除沙箱目录。"""
        for p in self.root.rglob("*"):
            if p.is_file():
                self.destroy(p)
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------- 状态 ----------
    def residual(self) -> int:
        """沙箱残留文件数（销毁后应为 0）。"""
        if not self.root.exists():
            return 0
        return sum(1 for p in self.root.rglob("*") if p.is_file())
