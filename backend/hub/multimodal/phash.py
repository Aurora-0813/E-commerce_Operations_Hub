# -*- coding: utf-8 -*-
"""
感知哈希（phash.py）
===================
pHash：把图片压成指纹，用于"去重"（同一张图只调一次 Vision）。

和 R007 的 Jaccard 是同一个思想，只不过对象从"文本"换成了"图片"。

算法（简化版）：
    1. 转灰度、缩到 N×N（丢掉细节，保留整体明暗结构）
    2. 算平均值
    3. 每个像素 > 平均值记 1，否则记 0 → N² 位二进制
    4. 转成十六进制字符串（方便存 TEXT 列）

为什么默认用 16×16（256 位）而不是 8×8（64 位）：
    8×8 太粗——白底黑衣的商品图缩到 8×8 灰度后几乎一样，
    实测 50 张不同图片里 13 张被误判成"同一张"，
    导致瑕疵图复用了正常图的标签（漏检）。
    16×16 后区分度足够，误判基本消失。
"""
from PIL import Image


def phash(path: str, size: int = 16) -> str:
    """计算图片的感知哈希（size×size 位，返回对应长度的十六进制）。"""
    img = Image.open(path).convert("L").resize((size, size), Image.LANCZOS)
    pixels = list(img.getdata())

    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)

    hex_len = (size * size + 3) // 4      # 256 位 → 64 个十六进制字符
    return f"{int(bits, 2):0{hex_len}x}"


def hamming(a: str, b: str) -> int:
    """两个十六进制指纹的汉明距离（不同的位数）。"""
    return bin(int(a, 16) ^ int(b, 16)).count("1")
