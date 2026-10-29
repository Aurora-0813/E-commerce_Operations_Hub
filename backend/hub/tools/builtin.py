# -*- coding: utf-8 -*-
"""
内置工具（builtin.py）
=====================
P0 的 4 个业务工具 + 1 个补偿工具，覆盖 L0~L3 四个风险等级。

    catalog.query_price     L0  查当前价（只读）
    review.draft_reply      L1  写差评回复草稿（不发出，无对外影响）
    listing.update_desc     L2  改详情页描述（有补偿）
    listing.restore_desc    L2  补偿：把描述改回去（只由执行链调用）
    product.update_price    L3  改价（无补偿，必须人工批准）

**每个工具都必须有 verify**：
    不是"handler 没报错就算成功"，而是**重新查一遍真实状态**。
    这是反幻觉的第三道防线——工具的返回值也可能是它"以为"的成功。

状态存在哪（不新建"平台状态表"，因为它会和 M4 读的 `spu_catalog` 分叉）：
    价格  → `spu_catalog.price_ref`（M4 的价格背离读的就是这一列）+ `price_snapshot` 留历史
    描述  → `spu_catalog.description`
    草稿  → `review_reply_draft`
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from hub.persistence.db import get_session
from hub.persistence.models import PriceSnapshot, ReviewReplyDraft, SpuCatalog
from hub.tools.spec import (
    CallContext,
    RiskLevel,
    ToolSpec,
    VerifyResult,
    register,
)


# ==================== 参数模型 ====================

class _Params(BaseModel):
    """所有工具参数的基类。

    `extra="forbid"`：**多余字段直接报错**，不静默忽略。
    这是"参数强校验"的一部分——如果提案里带了工具不认识的字段，
    说明生成提案的代码和工具有分歧，应该当场暴露，而不是悄悄丢掉。
    """
    model_config = ConfigDict(extra="forbid")


class QueryPriceParams(_Params):
    """查价参数。"""
    platform: str = Field(description="平台：taobao / jd")
    spu_id: str = Field(description="平台商品 ID，如 TB-0001")


class DraftReplyParams(_Params):
    """写回复草稿的参数。

    content 由**代码**用模板/LLM 生成好再传进来（工具本身不调 LLM），
    这样工具是确定性的、可重放的。
    """
    cluster_id: str = Field(description="问题簇 ID")
    review_ids: list[str] = Field(min_length=1, description="要回复的评价 ID 列表")
    content: str = Field(min_length=10, max_length=500, description="回复正文")
    compliance_flags: list[str] = Field(default_factory=list,
                                        description="合规检查命中的问题（空=合规）")


class UpdateDescParams(_Params):
    """改详情页描述。"""
    platform: str = Field(description="平台：taobao / jd")
    spu_id: str = Field(description="平台商品 ID")
    description: str = Field(min_length=5, max_length=1000, description="新的详情页描述")


class RestoreDescParams(_Params):
    """补偿：把描述改回旧值。"""
    platform: str = Field(description="平台：taobao / jd")
    spu_id: str = Field(description="平台商品 ID")
    description: str | None = Field(default=None, max_length=1000,
                                    description="要恢复的旧描述（null = 恢复成空）")


class UpdatePriceParams(_Params):
    """改价。"""
    platform: str = Field(description="平台：taobao / jd")
    spu_id: str = Field(description="平台商品 ID")
    new_price: Decimal = Field(gt=0, le=1000000, description="新价格（元）")


# ==================== 小工具函数 ====================

def _load_spu(s, platform: str, spu_id: str) -> SpuCatalog | None:
    """按 (platform, spu_id) 取商品行。

    `spu_catalog` 的主键叫 canonical_spu_id，但实际存的是平台商品 ID
    （见 models.py 的注释），所以这里按它查。
    """
    return s.scalars(
        select(SpuCatalog)
        .where(SpuCatalog.canonical_spu_id == spu_id)
        .where(SpuCatalog.platform == platform)
        .limit(1)
    ).first()


# ==================== L0：查价 ====================

def _query_price(args: dict, ctx: CallContext) -> dict:
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        if row is None:
            raise LookupError(f"商品不存在：{args['platform']}/{args['spu_id']}")
        return {
            "platform": row.platform,
            "spu_id": row.canonical_spu_id,
            "title": row.title,
            "price": str(row.price_ref) if row.price_ref is not None else None,
        }


def _verify_price_query(args: dict, ctx: CallContext, result: dict) -> VerifyResult:
    """只读工具：能读到就算成功。"""
    try:
        got = _query_price(args, ctx)
    except LookupError as e:
        return VerifyResult(False, {"error": str(e)})
    return VerifyResult(True, {"price": got["price"]})


# ==================== L1：写回复草稿 ====================

def _draft_reply(args: dict, ctx: CallContext) -> dict:
    """写一条回复草稿（不发送，只是草稿）。"""
    with get_session() as s:
        draft = ReviewReplyDraft(
            cluster_id=UUID(args["cluster_id"]) if args.get("cluster_id") else None,
            review_ids=[UUID(r) for r in args["review_ids"]],
            content=args["content"],
            compliance_flags=args.get("compliance_flags") or [],
            status="DRAFT",
        )
        s.add(draft)
        s.flush()
        draft_id = str(draft.id)
        s.commit()
    return {"draft_id": draft_id, "status": "DRAFT"}


def _verify_draft_reply(args: dict, ctx: CallContext, result: dict) -> VerifyResult:
    """回查：草稿真的在库里，而且内容一致。

    result 只用来拿 draft_id（知道查哪一行），不当作成功证据。
    """
    draft_id = result.get("draft_id")
    if not draft_id:
        return VerifyResult(False, {"error": "handler 没返回 draft_id"})
    with get_session() as s:
        row = s.get(ReviewReplyDraft, UUID(draft_id))
        if row is None:
            return VerifyResult(False, {"error": "草稿不存在"})
        ok = row.content == args["content"] and row.status == "DRAFT"
        return VerifyResult(ok, {"status": row.status, "len": len(row.content or "")})


# ==================== L2：改详情页描述 ====================

def _update_desc(args: dict, ctx: CallContext) -> dict:
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        if row is None:
            raise LookupError(f"商品不存在：{args['platform']}/{args['spu_id']}")
        old = row.description
        row.description = args["description"]
        s.commit()
    # 把旧值一起返回：执行链失败时要拿它做补偿
    return {"old_description": old, "new_description": args["description"]}


def _verify_update_desc(args: dict, ctx: CallContext, result: dict) -> VerifyResult:
    """回查：重新读库，描述真的是新值吗？"""
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        if row is None:
            return VerifyResult(False, {"error": "商品不存在"})
        actual = row.description
    ok = actual == args["description"]
    return VerifyResult(ok, {
        "expected": args["description"][:40],
        "actual": (actual or "")[:40],
    })


def _restore_desc(args: dict, ctx: CallContext) -> dict:
    """补偿动作：把描述改回执行前的值。"""
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        if row is None:
            raise LookupError(f"商品不存在：{args['platform']}/{args['spu_id']}")
        row.description = args.get("description")
        s.commit()
    return {"restored": args.get("description")}


def _verify_restore_desc(args: dict, ctx: CallContext, result: dict) -> VerifyResult:
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        actual = row.description if row else None
    return VerifyResult(actual == args.get("description"), {"actual": (actual or "")[:40]})


# ==================== L3：改价 ====================

def _update_price(args: dict, ctx: CallContext) -> dict:
    """改价 + 留一条价格快照。

    **没有补偿**：钱的事回不去，所以这个工具永远走 HITL（人工批准后才执行）。
    """
    new_price = Decimal(str(args["new_price"]))
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        if row is None:
            raise LookupError(f"商品不存在：{args['platform']}/{args['spu_id']}")
        old_price = row.price_ref
        row.price_ref = new_price
        s.add(PriceSnapshot(
            canonical_spu_id=args["spu_id"],
            platform=args["platform"],
            price=new_price,
            source="tool:product.update_price",
            captured_at=datetime.now(timezone.utc),
        ))
        s.commit()
    return {
        "old_price": str(old_price) if old_price is not None else None,
        "new_price": str(new_price),
    }


def _verify_update_price(args: dict, ctx: CallContext, result: dict) -> VerifyResult:
    """回查：重新读价，等于目标价吗？（不信 handler 的返回值）"""
    expected = Decimal(str(args["new_price"])).quantize(Decimal("0.01"))
    with get_session() as s:
        row = _load_spu(s, args["platform"], args["spu_id"])
        actual = row.price_ref if row else None
    ok = actual is not None and Decimal(str(actual)).quantize(Decimal("0.01")) == expected
    return VerifyResult(ok, {
        "expected": str(expected),
        "actual": str(actual) if actual is not None else None,
    })


# ==================== 注册 ====================

def register_all() -> None:
    """注册全部内置工具（幂等：重复调用不会报错）。"""
    from hub.tools.spec import get as _get

    if _get("catalog.query_price"):
        return

    register(ToolSpec(
        name="catalog.query_price",
        version="1.0.0",
        description="查询某个商品在某个平台的当前售价（只读，可以直接执行）",
        params_model=QueryPriceParams,
        risk_level=RiskLevel.L0,
        handler=_query_price,
        verify=_verify_price_query,
        outcome_metrics=(),
        timeout_s=2.0,
    ))

    register(ToolSpec(
        name="review.draft_reply",
        version="1.0.0",
        description="针对一个问题簇的差评，生成一条回复草稿（只是草稿，不会发送给买家）",
        params_model=DraftReplyParams,
        risk_level=RiskLevel.L1,
        handler=_draft_reply,
        verify=_verify_draft_reply,
        evidence_types=frozenset({"issue_cluster"}),
        outcome_metrics=("negative_review_rate",),
        timeout_s=5.0,
    ))

    register(ToolSpec(
        name="listing.update_desc",
        version="1.0.0",
        description="修改商品的详情页描述（比如把面料成分、尺码表写清楚），改坏了可以恢复",
        params_model=UpdateDescParams,
        risk_level=RiskLevel.L2,
        handler=_update_desc,
        verify=_verify_update_desc,
        required_role="operator",
        evidence_types=frozenset({"issue_cluster"}),
        compensate="listing.restore_desc",
        outcome_metrics=("negative_review_rate", "conversion_rate"),
        timeout_s=5.0,
    ))

    register(ToolSpec(
        name="listing.restore_desc",
        version="1.0.0",
        description="[补偿] 把商品详情页描述恢复到修改前的值（只由执行链在失败时调用）",
        params_model=RestoreDescParams,
        risk_level=RiskLevel.L2,
        handler=_restore_desc,
        verify=_verify_restore_desc,
        required_role="operator",
        timeout_s=5.0,
    ))

    register(ToolSpec(
        name="product.update_price",
        version="1.0.0",
        description="修改商品售价（涉及资金、不可撤销，必须人工批准后才能执行）",
        params_model=UpdatePriceParams,
        risk_level=RiskLevel.L3,
        handler=_update_price,
        verify=_verify_update_price,
        required_role="manager",
        evidence_types=frozenset({"anomaly"}),
        compensate=None,          # 不可逆：没有补偿动作
        outcome_metrics=("conversion_rate", "gmv"),
        timeout_s=5.0,
    ))
