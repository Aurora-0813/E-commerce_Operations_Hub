# -*- coding: utf-8 -*-
"""
多模态契约（multimodal.py）
==========================
对应 M2 多模态沙箱（开发指南 05）：一张买家秀图片，被 Vision 模型"读成"结构化标签。

合规要点：这里只存"标签"（破损/色差/严重度），绝不存原图、base64、缩略图。
"""
from typing import Literal

from pydantic import BaseModel, Field


class VisionTags(BaseModel):
    """Vision 模型对一张图片的抽取结果（阅后即焚：图片用完销毁，只留这些标签）。"""

    is_product_image: bool = True
    # 是不是商品图。表情包、无关图、宣传图 = False（这类图不作数）

    defect_type: Literal["none", "破损", "污渍", "色差", "开线", "错发", "尺码不符", "其他"] = "none"
    # 缺陷类型（封闭枚举）。none = 无缺陷

    severity: int = Field(default=0, ge=0, le=3)
    # 严重度 0~3：0 无、1 轻微、2 明显、3 严重

    color_deviation: float = Field(default=0.0, ge=0, le=1)
    # 色差程度 0~1（0=无色差，1=严重色差）

    ocr_text: str | None = None
    # 图片上识别出的文字（可空），如吊牌上的"100%棉"

    confidence: float = Field(ge=0, le=1)
    # 置信度 0~1。低于 0.5 的结果只标 low_confidence，不硬采信
