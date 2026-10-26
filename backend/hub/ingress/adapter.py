# -*- coding: utf-8 -*-
"""
适配器主流程（adapter.py）
=========================
一个文件进来，出去三样东西：
    RawBatch        批次信息（文件指纹、sheet、坏行）
    list[Record]    归一化后的业务记录
    MappingReport   导入质量报告

流程：
    1. 算文件 SHA256（幂等去重）
    2. 读所有 sheet
    3. 每个 sheet 探测表头、逐行转 Record
    4. 空行/合计行跳过；坏行记录原因、不中断
    5. PII：昵称丢弃、正文脱敏
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from hub.contracts import BadRow, MappingReport, RawBatch, RawSheet, Record
from hub.ingress.mapping import get_mapping, known_columns
from hub.ingress.normalize import (
    parse_date,
    parse_datetime,
    parse_int,
    parse_money,
    parse_rating,
)
from hub.ingress.pii import is_pii_field, mask_text
from hub.ingress.reader import detect_header, is_empty_row, is_total_row, read_sheets

# 图片字段的分隔符：逗号、分号、竖线（中英文都算）、换行
_IMG_SEP = re.compile(r"[,;|，；\n\r]+")

# 一条评价最多保留几张图（买家秀实际上限约 9 张）
MAX_IMAGES = 9


def _parse_image_urls(value) -> list[str]:
    """把导出里的图片字段拆成 URL 列表。

    真实导出的"图片地址"可能是：
        - 单张：https://.../a.jpg
        - 多张：https://.../a.jpg,https://.../b.jpg（或分号/换行分隔）
    统一拆成列表，最多保留 9 张。
    """
    if value is None:
        return []
    parts = [p.strip() for p in _IMG_SEP.split(str(value))]
    return [p for p in parts if p][:MAX_IMAGES]


def _sha256(path: Path) -> str:
    """算文件 SHA256（用于幂等：同一个文件不重复导入）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_review(raw: dict, platform: str) -> tuple[Record | None, str | None]:
    """把一行评价的原始字段，变成 Record（失败返回原因）。"""
    # ---- 必填校验 ----
    content = raw.get("content")
    if content is None or not str(content).strip():
        return None, "EMPTY_CONTENT"

    rating = parse_rating(raw.get("rating"), platform)
    if rating is None:
        return None, "INVALID_RATING"

    review_time = parse_datetime(raw.get("review_time"))
    if review_time is None:
        return None, "INVALID_TIME"

    # ---- 归一化字段 ----
    # 图片：导出里可能是单张 URL，也可能是多张（逗号/分号/换行分隔），最多保留 9 张
    image_urls = _parse_image_urls(raw.get("image_url"))

    fields = {
        "platform_spu_id": str(raw.get("platform_spu_id") or ""),
        "sku_id": raw.get("sku_id"),
        "title": raw.get("title"),
        "attr": raw.get("attr"),
        "tags": raw.get("tags"),
        "content": mask_text(str(content).strip()),     # 正文脱敏（手机号/微信号）
        "append_content": mask_text(str(raw.get("append_content") or "")),
        "has_image": bool(image_urls),
        "image_urls": image_urls,
    }
    # 注意：评分 rating 是 Record 的顶层字段，不放 fields
    # 昵称是 PII，直接丢弃（不放进 fields）

    monetary: dict = {}
    price = parse_money(raw.get("price"))
    if price is not None:
        monetary["price"] = price

    record = Record(
        record_id=uuid4().hex,
        platform=platform,
        data_type="review",
        platform_id=str(raw.get("platform_review_id") or ""),
        canonical_spu_id=None,          # 同款对齐是后面 M11 的事
        rating=rating,                  # 顶层字段：归一后的 1~5
        fields=fields,
        monetary=monetary,
        ts=review_time,
        impl="ingress.v1",
    )
    return record, None


def _build_product(raw: dict, platform: str) -> tuple[Record | None, str | None]:
    """一行商品数据 → Record。"""
    spu_id = raw.get("platform_spu_id")
    if spu_id is None or not str(spu_id).strip():
        return None, "MISSING_SPU_ID"

    fields = {
        "platform_spu_id": str(spu_id),
        "title": raw.get("title"),
        "category": raw.get("category"),
        "stock": parse_int(raw.get("stock")),
        "monthly_sales": parse_int(raw.get("monthly_sales")),
    }
    monetary: dict = {}
    price = parse_money(raw.get("price"))
    if price is not None:
        monetary["price"] = price

    record = Record(
        record_id=uuid4().hex,
        platform=platform,
        data_type="product",
        platform_id=str(spu_id),
        fields=fields,
        monetary=monetary,
        ts=None,
        impl="ingress.v1",
    )
    return record, None


def _build_sales(raw: dict, platform: str) -> tuple[Record | None, str | None]:
    """一行销售数据 → Record。"""
    spu_id = raw.get("platform_spu_id")
    if spu_id is None or not str(spu_id).strip():
        return None, "MISSING_SPU_ID"
    stat_date = parse_date(raw.get("stat_date"))
    if stat_date is None:
        return None, "INVALID_DATE"

    fields = {
        "platform_spu_id": str(spu_id),
        "title": raw.get("title"),
        "stat_date": stat_date.isoformat(),
        "units": parse_int(raw.get("units")),
        "uv": parse_int(raw.get("uv")),
        "refund_units": parse_int(raw.get("refund_units")),
    }
    monetary: dict = {}
    for key in ("gmv", "refund_amount"):
        v = parse_money(raw.get(key))
        if v is not None:
            monetary[key] = v

    record = Record(
        record_id=uuid4().hex,
        platform=platform,
        data_type="sales",
        platform_id=f"{spu_id}:{stat_date.isoformat()}",
        fields=fields,
        monetary=monetary,
        ts=datetime.combine(stat_date, datetime.min.time()),
        impl="ingress.v1",
    )
    return record, None


_BUILDERS = {
    "review": _build_review,
    "product": _build_product,
    "sales": _build_sales,
}


def read_file(path: str | Path, platform: str, data_type: str):
    """主入口：读一个文件 → (RawBatch, records, MappingReport)。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")

    mapping = get_mapping(platform, data_type)
    known = known_columns(platform, data_type)
    builder = _BUILDERS[data_type]

    batch_id = uuid4()
    sheets_out: list[RawSheet] = []
    records: list[Record] = []
    report = MappingReport(total=0, row_ok=0, row_bad=0)
    pii_dropped = 0
    coverage: dict[str, int] = {}

    for sheet_name, rows in read_sheets(path):
        header_idx, col_map = detect_header(rows, known)
        if not col_map:
            report.warnings.append(f"sheet「{sheet_name}」没找到已知表头，已跳过")
            continue

        bad_rows: list[BadRow] = []
        row_count = 0

        for row_no, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            if is_empty_row(row) or is_total_row(row):
                continue
            row_count += 1
            report.total += 1

            # 原始列名 -> 值
            raw: dict = {}
            for col_name, col_idx in col_map.items():
                cell = row[col_idx] if col_idx < len(row) else None
                raw[mapping[col_name]] = cell

            record, reason = builder(raw, platform)
            if reason is not None:
                bad_rows.append(BadRow(
                    line_no=row_no,
                    raw=str([c for c in row if c is not None])[:200],
                    column=None,
                    reason=reason,
                ))
                report.row_bad += 1
                continue

            # PII 统计：昵称被丢弃
            if "nickname" in raw and raw["nickname"]:
                pii_dropped += 1

            # 字段覆盖率统计
            for k, v in record.fields.items():
                if v not in (None, "", [], {}):
                    coverage[k] = coverage.get(k, 0) + 1
            if record.rating is not None:      # rating 是顶层字段，单独统计
                coverage["rating"] = coverage.get("rating", 0) + 1

            records.append(record)
            report.row_ok += 1

        sheets_out.append(RawSheet(
            name=sheet_name,
            header_row=header_idx + 1,                     # 1-based
            columns={mapping[k]: k for k in col_map},      # 归一字段 -> 原始列名
            row_count=row_count,
            bad_rows=bad_rows,
        ))

    # ---- 汇总报告 ----
    if report.total == 0:
        report.warnings.append("没有读到任何数据行")

    report.field_coverage = {
        k: round(v / report.row_ok, 4) for k, v in coverage.items()
    } if report.row_ok else {}
    report.pii_stats = {"nickname_dropped": pii_dropped}
    # 同款对齐还没做，所以全部算"未映射"
    report.unmapped_rate = 1.0 if report.row_ok else 0.0

    batch = RawBatch(
        batch_id=batch_id,
        platform=platform,
        data_type=data_type,
        file_sha256=_sha256(path),
        file_name=path.name,
        encoding="utf-8",           # xlsx 内部是 XML，无编码问题
        sheets=sheets_out,
    )
    return batch, records, report
