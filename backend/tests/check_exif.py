# -*- coding: utf-8 -*-
"""
验证沙箱的元数据剥离（隐私红线）。

测试逻辑：
    ① 造一张"带 GPS 坐标 + 设备型号"的 JPEG（模拟买家 iPhone 拍的照片）
    ② 确认它**确实有** EXIF（不然测试就是假的）
    ③ 过一遍 Sandbox.ingest
    ④ 确认沙箱里的图**没有任何** EXIF / GPS / XMP
    ⑤ 确认像素尺寸没变（证明只丢元数据，没毁图）
    ⑥ 确认退出后沙箱残留为 0

运行（在 backend/ 目录下）：
    python tests/check_exif.py
"""
import sys
import tempfile
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from PIL import Image                      # noqa: E402

from hub.multimodal.sandbox import Sandbox   # noqa: E402

# EXIF 标签编号
TAG_MAKE = 0x010F        # 设备厂商
TAG_MODEL = 0x0110       # 设备型号
TAG_GPS = 0x8825         # GPS IFD


def make_gps_jpeg(path: Path) -> bool:
    """造一张带 GPS + 设备信息的 JPEG（模拟真实手机照片）。

    :return: True = GPS 也写进去了；False = 只写了设备信息（Pillow 版本不支持 GPS）
    """
    img = Image.new("RGB", (64, 64), (200, 30, 30))

    exif = Image.Exif()
    exif[TAG_MAKE] = "TestPhone"
    exif[TAG_MODEL] = "TestPhone 15 Pro"

    # GPS IFD：纬度 39.9042°N，经度 116.4074°E（天安门附近）
    # 注意：Pillow 要求 rational 用**浮点数**传，嵌套元组 (39,1) 会被当成数值而报错
    try:
        exif[TAG_GPS] = {
            1: "N",
            2: (39.0, 54.0, 15.12),
            3: "E",
            4: (116.0, 24.0, 26.64),
        }
        img.save(path, "JPEG", quality=95, exif=exif)
        return True
    except Exception as e:                    # noqa: BLE001
        print(f"  （写 GPS 失败：{e}）")
        print("  （降级为只写设备信息——同样能验证元数据剥离）")
        exif2 = Image.Exif()
        exif2[TAG_MAKE] = "TestPhone"
        exif2[TAG_MODEL] = "TestPhone 15 Pro"
        img.save(path, "JPEG", quality=95, exif=exif2)
        return False


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        src_dir = Path(td) / "upload"
        src_dir.mkdir()
        src = src_dir / "O1CN01test.jpg"
        has_gps = make_gps_jpeg(src)

        # ---------- ① 证明输入确实有隐私数据 ----------
        with Image.open(src) as im:
            exif_in = im.getexif()
            make_in = exif_in.get(TAG_MAKE)
            gps_in = exif_in.get_ifd(TAG_GPS)
        print("=== ① 原始图片（模拟买家上传）===")
        print(f"  文件大小 : {src.stat().st_size} 字节")
        print(f"  设备厂商 : {make_in!r}")
        print(f"  GPS 数据 : {dict(gps_in) if gps_in else '（无）'}")

        if not make_in:
            print("\n❌ 造图失败：原始图没有 EXIF，测试没有意义")
            return

        # ---------- ② 过沙箱 ----------
        print("\n=== ② 进沙箱（ingest 时剥离）===")
        with Sandbox() as sb:
            n = sb.ingest(src_dir)
            print(f"  导入 {n} 张，剥离 {sb.stripped} 张")

            inside = sb.find("O1CN01test.jpg")
            if inside is None:
                print("❌ 沙箱里找不到图片")
                return

            with Image.open(inside) as im:
                exif_out = im.getexif()
                make_out = exif_out.get(TAG_MAKE)
                gps_out = exif_out.get_ifd(TAG_GPS)
                size_out = im.size
                info_out = dict(im.info)

            print(f"\n=== ③ 沙箱里的图片（已剥离）===")
            print(f"  文件大小 : {inside.stat().st_size} 字节")
            print(f"  设备厂商 : {make_out!r}")
            print(f"  GPS 数据 : {dict(gps_out) if gps_out else '（无）'}")
            print(f"  元数据键 : {sorted(info_out.keys())}")

            # ---------- 断言 ----------
            print("\n=== ④ 判定 ===")
            ok = True

            if make_out is None:
                print("  ✅ 设备型号已清除")
            else:
                print(f"  ❌ 设备型号还在：{make_out!r}")
                ok = False

            if not gps_out:
                print("  ✅ GPS 已清除")
            else:
                print(f"  ❌ GPS 还在：{dict(gps_out)}")
                ok = False

            leaked = [k for k in ("exif", "xmp", "icc_profile", "xml")
                      if info_out.get(k)]
            if not leaked:
                print("  ✅ 无 EXIF / XMP / ICC 残留")
            else:
                print(f"  ❌ 仍有元数据键：{leaked}")
                ok = False

            if size_out == (64, 64):
                print("  ✅ 像素尺寸未变（64×64，只丢元数据没毁图）")
            else:
                print(f"  ❌ 尺寸变了：{size_out}")
                ok = False

        # ---------- ⑤ 退出后残留 ----------
        residual = sb.residual()
        print(f"\n=== ⑤ 沙箱销毁后残留：{residual} 个文件 ===")
        if residual == 0:
            print("  ✅ 残留 0")
        else:
            print(f"  ❌ 残留 {residual}")
            ok = False

        print("\n" + ("✅ 全部通过：元数据剥离生效" if ok else "❌ 有问题"))


if __name__ == "__main__":
    main()
