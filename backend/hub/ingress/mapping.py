# -*- coding: utf-8 -*-
"""
字段映射（mapping.py）
=====================
平台导出的列名千奇百怪（淘宝叫"订单编号"，京东叫"评论ID"），
这里把"平台列名 → 统一字段名"的对应关系集中管理。

好处：
    - 新增一个平台，只加一张映射表，适配器代码不动；
    - 表头探测时，用这些"已知列名"来判断哪一行是表头。
"""
from typing import Literal

# 统一字段名（所有平台最终都归到这些名字）
CANONICAL = {
    # 通用
    "platform_review_id", "platform_spu_id", "sku_id",
    "rating", "content", "review_time",
    "title", "attr", "tags",
    "append_content", "append_time", "image_url",
    "price", "nickname",
    # 商品/销售
    "category", "stock", "monthly_sales",
    "stat_date", "gmv", "units", "uv", "refund_amount", "refund_units",
}

# 平台 + 数据类型 → 映射表（原始列名 → 统一字段名）
MAPPINGS: dict[tuple[str, str], dict[str, str]] = {
    # ---------------- 淘宝评价 ----------------
    ("taobao", "review"): {
        "订单编号": "platform_review_id",
        "商品ID": "platform_spu_id",
        "商品标题": "title",
        "宝贝属性": "attr",
        "评价等级": "rating",
        "评价内容": "content",
        "评价时间": "review_time",
        "买家昵称": "nickname",
        "追加评价": "append_content",
        "追加评价时间": "append_time",
        "图片地址": "image_url",
        "商品价格": "price",
    },
    # ---------------- 京东评价 ----------------
    ("jd", "review"): {
        "评论ID": "platform_review_id",
        "商品编号": "platform_spu_id",
        "商品名称": "title",
        "SKU编号": "sku_id",
        "评价星级": "rating",
        "评价内容": "content",
        "评价时间": "review_time",
        "用户昵称": "nickname",
        "评价标签": "tags",
        "追评内容": "append_content",
        "追评时间": "append_time",
        "晒图URL": "image_url",
        "商品单价": "price",
    },
    # ---------------- 淘宝商品 ----------------
    ("taobao", "product"): {
        "商品ID": "platform_spu_id",
        "商品标题": "title",
        "类目": "category",
        "价格": "price",
        "库存": "stock",
        "月销量": "monthly_sales",
    },
    # ---------------- 京东商品 ----------------
    ("jd", "product"): {
        "商品编号": "platform_spu_id",
        "商品名称": "title",
        "类目": "category",
        "京东价": "price",
        "库存": "stock",
        "近30天销量": "monthly_sales",
    },
    # ---------------- 淘宝销售（生意参谋） ----------------
    ("taobao", "sales"): {
        "日期": "stat_date",
        "商品ID": "platform_spu_id",
        "商品名称": "title",
        "支付金额": "gmv",
        "支付买家数": "units",
        "访客数": "uv",
        "退款金额": "refund_amount",
        "退款笔数": "refund_units",
    },
    # ---------------- 京东销售（商智） ----------------
    ("jd", "sales"): {
        "日期": "stat_date",
        "商品编号": "platform_spu_id",
        "商品名称": "title",
        "成交金额": "gmv",
        "成交件数": "units",
        "访客数": "uv",
        "退款金额": "refund_amount",
        "退款件数": "refund_units",
    },
}


def get_mapping(platform: str, data_type: str) -> dict[str, str]:
    """取某个平台+类型的字段映射；没有就报错（提示支持哪些）。"""
    key = (platform, data_type)
    if key not in MAPPINGS:
        supported = sorted(f"{p}/{t}" for p, t in MAPPINGS)
        raise KeyError(f"不支持的平台/类型：{key}，支持：{supported}")
    return MAPPINGS[key]


def known_columns(platform: str, data_type: str) -> set[str]:
    """该平台+类型的所有已知列名（用于表头探测）。"""
    return set(get_mapping(platform, data_type).keys())
