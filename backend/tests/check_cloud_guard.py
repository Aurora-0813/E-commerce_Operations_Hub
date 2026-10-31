# -*- coding: utf-8 -*-
"""
云端数据外发闸门测试（check_cloud_guard.py）—— 阶段 1.2

纯函数测试，**不需要真连云端**。验 9 条：
    1. 本地地址不算出网 → 一律放行、不脱敏
    2. 云端 + 图片 → 拒绝（三条铁律①）
    3. 云端 + 评价原文 → 默认拒绝（三条铁律②）
    4. 云端 + 原文 + allow_text=True → 放行，且 PII 被脱敏
    5. 云端 + 聚合数字 → 放行（三条铁律③）
    6. 未知类别 → 拒绝（不允许"没写分支就默认放行"）
    7. is_cloud 判定：localhost / 127.0.0.1 / ::1 → 否；api.deepseek.com → 是
    8. 脱敏不误伤正常文本（"我女儿穿 110 码" 不该被改）
    9. 手机号/身份证/邮箱/长单号 都能被替换

跑法：python tests/check_cloud_guard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.core.cloud_guard import (
    KIND_AGGREGATE,
    KIND_IMAGE,
    KIND_TEXT,
    CloudBlocked,
    guard,
    is_cloud,
    redact_pii,
)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def blocked(fn) -> tuple[bool, str]:
    """跑 fn，返回 (是否被拒绝, 错误信息)。"""
    try:
        fn()
        return False, ""
    except CloudBlocked as e:
        return True, str(e)


def main() -> int:
    print("=" * 62)
    print("  云端数据外发闸门")
    print("=" * 62)

    # ---------- 1. 本地不算出网 ----------
    print("\n[1] 本地地址：一律放行，不脱敏")
    r = guard("http://127.0.0.1:11434/v1", KIND_TEXT, "手机 13800138000", allow_text=False)
    check("本地放行", r.allowed and not r.is_cloud)
    check("本地不脱敏（原文原样）", r.text == "手机 13800138000", r.text)

    # ---------- 2. 图片永不出网 ----------
    print("\n[2] 云端 + 图片 → 拒绝")
    is_blocked, msg = blocked(lambda: guard("https://api.deepseek.com/v1", KIND_IMAGE, ""))
    check("图片被拒", is_blocked, msg[:50])

    # ---------- 3. 评价原文默认不出网 ----------
    print("\n[3] 云端 + 评价原文 → 默认拒绝")
    is_blocked, msg = blocked(
        lambda: guard("https://api.deepseek.com/v1", KIND_TEXT, "掉色严重", allow_text=False)
    )
    check("原文被拒（allow_text=False）", is_blocked, msg[:50])

    # ---------- 4. 允许后要脱敏 ----------
    print("\n[4] 云端 + 原文 + allow_text=True → 放行且脱敏")
    raw = "我手机 13800138000，订单 1234567890123456，邮箱 a@b.com，衣服掉色"
    r = guard("https://api.deepseek.com/v1", KIND_TEXT, raw, allow_text=True, do_redact=True)
    check("放行", r.allowed and r.is_cloud)
    check("手机号被替换", "13800138000" not in r.text, r.text[:70])
    check("长单号被替换", "1234567890123456" not in r.text)
    check("邮箱被替换", "a@b.com" not in r.text)
    check("业务语义保留（'衣服掉色'还在）", "掉色" in r.text)
    check("记录了脱敏类别", bool(r.redactions), ",".join(r.redactions))

    # ---------- 5. 聚合数字可出网 ----------
    print("\n[5] 云端 + 聚合数字 → 放行")
    r = guard("https://api.deepseek.com/v1", KIND_AGGREGATE, "差评率 41.5%，973 条", )
    check("聚合放行", r.allowed and r.is_cloud)
    check("聚合不脱敏", r.text == "差评率 41.5%，973 条")

    # ---------- 6. 未知类别拒绝 ----------
    print("\n[6] 未知类别 → 拒绝（不许默认放行）")
    is_blocked, msg = blocked(lambda: guard("https://api.deepseek.com/v1", "whatever", "x"))
    check("未知类别被拒", is_blocked, msg[:50])

    # ---------- 7. is_cloud 判定 ----------
    print("\n[7] is_cloud 判定")
    for url, want in [
        ("http://localhost:11434/v1", False),
        ("http://127.0.0.1:11434/v1", False),
        ("http://[::1]:11434/v1", False),
        ("https://api.deepseek.com/v1", True),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", True),
        ("http://192.168.1.9:11434/v1", True),   # 局域网也算出网：保守
    ]:
        got = is_cloud(url)
        check(f"{url} → {'云端' if want else '本地'}", got == want, f"实际={got}")

    # ---------- 8. 不误伤 ----------
    print("\n[8] 脱敏不误伤正常文本")
    txt = "我女儿穿 110 码，洗了一次就掉色，客服说可以退"
    out, hits = redact_pii(txt)
    check("正常文本未被改动", out == txt, out)
    check("没误报 PII", not hits, ",".join(hits))

    # ---------- 9. 四类 PII ----------
    print("\n[9] 四类 PII 都能识别")
    for name, sample in [
        ("手机号", "电话13812345678"),
        ("身份证", "证件110101199003071234"),
        ("邮箱", "邮箱 zhang@example.com"),
        # 18 位纯数字是**歧义**的：既可能是订单号，也可能是老式身份证。
        # 安全闸门的正确选择是"宁可标签不准，也不漏脱敏" ——
        # 所以这里只断言"被脱敏了"，不断言被标成哪一类。
        ("长单号（歧义，只要被脱敏即可）", "单号123456789012345678"),
    ]:
        out, hits = redact_pii(sample)
        check(f"{name} 被脱敏", bool(hits) and out != sample, f"{out}（{','.join(hits)}）")

    print("\n" + "=" * 62)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 62)
        return 1
    print("  全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
