# -*- coding: utf-8 -*-
"""
数据接入契约（ingress.py）
=========================
定义"数据从文件进来到变成结构化记录"这一路要用的数据结构。

对应开发指南 03（数据接入与 PII）：
    - RawBatch   一个导入文件解析后的整体结果
    - RawSheet   文件里单个 sheet 的解析结果
    - BadRow     解析失败的一行（坏行）
    - Record     一条归一化后的业务记录（评价/商品/订单）
    - MappingReport  导入质量报告
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BadRow(BaseModel):
    """一行解析失败的数据（"坏行"）。"""
    line_no: int                 # 第几行（从 1 开始，方便定位）
    raw: str                     # 原始内容（保留原文，事后能排查）
    column: str | None = None    # 是哪个字段出的错（可空，比如"整行格式错"就不知道列）
    reason: str                  # 失败原因（人话）


class RawSheet(BaseModel):
    """一个 sheet（Excel 工作表）解析后的结果。"""
    name: str                    # sheet 名，如 "评价数据"
    header_row: int              # 表头在第几行（真实导出第一行就是表头，这里是 1）
    columns: dict[str, str]      # 归一列名 -> 原始列名 的映射，如 {"rating": "评价星级"}
    row_count: int               # 数据行总数
    bad_rows: list[BadRow] = Field(default_factory=list)   # 坏行列表（默认空）


class RawBatch(BaseModel):
    """一个导入文件解析后的整体结果（一次导入 = 一个 RawBatch）。"""
    batch_id: UUID               # 本次导入的唯一编号
    platform: str                # 平台：taobao / jd
    data_type: str               # 数据类型：review / product / order / sales
    file_sha256: str             # 文件的 SHA256 指纹（用于判断"是不是同一个文件"，幂等去重）
    file_name: str               # 原始文件名
    encoding: str                # 探测出的编码，如 utf-8 / gbk
    sheets: list[RawSheet]       # 文件里的 sheet 列表（真实导出可能是多 sheet）
    declared_period: tuple[date, date] | None = None  # 导出时选的日期区间（可空）


class Record(BaseModel):
    """一条归一化后的业务记录（评价 / 商品 / 订单 都用它）。

    这是全项目最核心的数据结构：所有"脏数据清洗"之后，都会变成统一的 Record。
    """
    record_id: str               # 系统内部生成的唯一 ID
    platform: str                # 平台：taobao / jd
    data_type: str               # 数据类型：review / product / order
    platform_id: str             # 平台侧原始主键（如评价的订单编号、订单号）
    canonical_spu_id: str | None = None   # 同款对齐后的"统一商品ID"；None = 还没对齐到同款
    rating: int | None = None     # 评分 1~5（评价类专用；商品/销售类为空）
    fields: dict[str, Any] = Field(default_factory=dict)   # 归一化后的业务字段（键值对）
    monetary: dict[str, Decimal] = Field(default_factory=dict)  # 金额字段单独放，用 Decimal（防精度丢失）
    ts: datetime | None = None   # 业务时间（评价时间/下单时间），统一 Asia/Shanghai
    impl: str = "ingress.v1"     # 这个结构是哪个实现产出的（可追溯，见设计 §2.8 铁律）


class MappingReport(BaseModel):
    """导入质量报告：回答"这批数据质量怎么样"。"""
    total: int                   # 总行数
    row_ok: int                  # 解析成功的行数
    row_bad: int                 # 坏行数
    unmapped_rate: float = 0.0   # 未对齐到同款的比例（0~1）
    field_coverage: dict[str, float] = Field(default_factory=dict)  # 每个字段的有值率
    pii_stats: dict[str, int] = Field(default_factory=dict)          # 各 PII 列丢弃了多少（只计数）
    warnings: list[str] = Field(default_factory=list)                # 警告信息列表
