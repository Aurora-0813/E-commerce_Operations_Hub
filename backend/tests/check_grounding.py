# -*- coding: utf-8 -*-
"""
诊断脚本：验证两件事
    1. model_validator 能不能正确拿到 context（原文内容）
    2. 反幻觉校验能不能正确区分"真证据 / 假证据"

运行（在 backend/ 目录下）：
    python tests/check_grounding.py
"""
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path：这样从任何目录（终端/IDE）运行，都能 import hub
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from pydantic import BaseModel, ValidationInfo, model_validator

# ============ 第 1 步：测试 context 能否传入 ============
class Probe(BaseModel):
    x: int = 0

    @model_validator(mode="after")
    def v(self, info: ValidationInfo):
        # 关键：这里打印 info.context，看能不能拿到外面传进来的字典
        print("[1] info.context =", info.context)
        return self


print("=== 第 1 步：context 能否传入 ===")
Probe.model_validate({"x": 1}, context={"hello": "world"})
print("（如果上面打印出 {'hello': 'world'}，说明 context 传入正常）")

# ============ 第 2 步：测试我们项目的反幻觉校验 ============
from hub.contracts import Attribution   # noqa: E402  放到第 2 步导入，避免干扰

print("\n=== 第 2 步：真证据（应该通过）===")
a = Attribution.model_validate(
    {
        "root_cause": "quality",
        "confidence": 0.9,
        "explanation": "质量差",
        "evidence": [
            {"review_id": "r1", "quote": "衣服破洞", "signal": "text"},
            {"review_id": "r1", "quote": "还开线了", "signal": "text"},
        ],
    },
    context={"content_by_id": {"r1": "这件衣服破洞，还开线了，质量很差"}},
)
print("✅ 真证据通过，root_cause =", a.root_cause.value)

print("\n=== 第 3 步：假证据（应该报错拦截）===")
try:
    Attribution.model_validate(
        {
            "root_cause": "quality",
            "confidence": 0.9,
            "explanation": "测试",
            "evidence": [
                {"review_id": "r1", "quote": "完全不存在的原话", "signal": "text"}
            ],
        },
        context={"content_by_id": {"r1": "这件衣服破洞"}},
    )
    print("❌ 假证据竟然通过了（这是 bug！）")
except Exception as e:
    print("✅ 假证据被拦截，错误：", e)

print("\n=== 第 4 步：图片证据（quote = 缺陷类型，不在原文里）===")
a4 = Attribution.model_validate(
    {
        "root_cause": "quality",
        "confidence": 0.8,
        "explanation": "买家秀显示破洞，文字只说质量不行",
        "evidence": [
            {"review_id": "r1", "quote": "破损", "signal": "image"},
            {"review_id": "r2", "quote": "质量不行", "signal": "text"},
        ],
    },
    context={
        "content_by_id": {"r2": "质量不行，不想多说"},
        "feature_by_id": {"r1": "破损"},
    },
)
print("✅ 图片证据通过（quote='破损' 对上了 feature_by_id）")

print("\n=== 第 5 步：假的图片证据（应该拦截）===")
try:
    Attribution.model_validate(
        {
            "root_cause": "quality",
            "confidence": 0.5,
            "explanation": "测试",
            "evidence": [{"review_id": "r1", "quote": "污渍", "signal": "image"}],
        },
        context={"feature_by_id": {"r1": "破损"}},
    )
    print("❌ 假图片证据竟然通过了（这是 bug！）")
except Exception as e:
    print("✅ 假图片证据被拦截，错误：", e)

print("\n=== 第 6 步：评分证据 ===")
try:
    a6 = Attribution.model_validate(
        {
            "root_cause": "quality",
            "confidence": 0.5,
            "explanation": "一星差评",
            "evidence": [{"review_id": "r1", "quote": "1", "signal": "rating"}],
        },
        context={"rating_by_id": {"r1": 1}},
    )
    print("✅ 评分证据通过，root_cause =", a6.root_cause.value)
except Exception as e:
    print("❌ 评分证据失败了：", e)

print("\n=== 第 7 步：评分证据写错数字（应该拦截）===")
try:
    Attribution.model_validate(
        {
            "root_cause": "quality",
            "confidence": 0.5,
            "explanation": "测试",
            "evidence": [{"review_id": "r1", "quote": "5", "signal": "rating"}],
        },
        context={"rating_by_id": {"r1": 1}},
    )
    print("❌ 错误评分竟然通过了")
except Exception as e:
    print("✅ 错误评分被拦截，错误：", e)

print("\n诊断结束。把上面的输出全部贴给我。")
