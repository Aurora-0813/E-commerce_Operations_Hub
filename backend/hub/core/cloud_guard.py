# -*- coding: utf-8 -*-
"""
云端数据外发闸门（cloud_guard.py）—— 阶段 1.2
=============================================
**目标：让"什么数据能出网"成为一条可执行、可审计的规则，而不是开发者的自觉。**

为什么需要它：
    项目的卖点之一是"用户数据不出网"。但上云能显著提升模型能力，
    所以必须有闸门 —— 否则"某次顺手把评价原文发给云端"不会被任何人发现。

三条铁律（对应 docs/改进路线图.md §二）：
    ① 图片永不出网           → 本模块直接拒绝；vision.py 也只连本地（双保险）
    ② 评价原文默认不出网     → `HUB_CLOUD__ALLOW_TEXT` 默认 False
    ③ 聚合统计可上云         → 只有数字，不可识别到个人

判断"什么算泄露"不是看"原始/加工"，而是三问（§二）：
    可识别性（还能不能定位到人）/ 可逆性（能不能还原）/ 授权（平台条款允不允许）
    → 所以"删掉昵称"**不等于**脱敏（正文里"我女儿穿 110 码"照样能定位）✅

设计取舍：
    · 只做"防误发"，不防"故意发" —— 真合规靠法务和平台条款，不靠代码
    · 每次出网记一条 span（kind / 是否脱敏 / 脱敏几处），供 `hub trace` 追溯
      （`audit_log` 落库放到阶段 4：那时才有真实的 actor）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------- 数据类别 ----------
KIND_AGGREGATE = "aggregate"   # 只有聚合数字（差评率 / 条数 / GMV）→ 可上云
KIND_TEXT = "text"             # 评价原文等用户生成内容 → 默认拒绝
KIND_IMAGE = "image"           # 图片 → 永远拒绝

# ---------- 本地地址判定 ----------
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")


class CloudBlocked(RuntimeError):
    """出网请求被闸门拒绝。"""


@dataclass
class GuardResult:
    """闸门结论。"""
    kind: str
    is_cloud: bool
    allowed: bool
    text: str                 # 可能被脱敏后的文本（allowed=False 时原样返回）
    redactions: list[str]     # 脱敏了哪几类（如 ["phone", "id_card"]）


# ---------------------------------------------------------------- 地址判定
def is_cloud(base_url: str) -> bool:
    """这个 base_url 算不算"出网"？

    只看主机名，不发起网络请求 —— 闸门不能因为网络问题而失效。

    三种写法的处理：
        http://localhost:11434/v1     → localhost
        http://127.0.0.1:11434/v1     → 127.0.0.1
        http://[::1]:11434/v1         → ::1        ← IPv6 带方括号，不能按 ":" 切
    局域网（192.168.x / 10.x）算**出网** —— 保守判定，宁可多拦。
    """
    rest = (base_url or "").split("://", 1)[-1]
    hostport = rest.split("/", 1)[0]
    if hostport.startswith("["):                     # IPv6 字面量：[::1]:11434
        end = hostport.find("]")
        host = hostport[1:end] if end > 0 else hostport
    else:                                            # 普通主机名/域名/IPv4
        host = hostport.split(":", 1)[0]
    return host.strip().lower() not in _LOCAL_HOSTS


# ---------------------------------------------------------------- PII 脱敏
# 和 hub/ingress/pii.py 的关系：
#   ingress 的 PII 检测是"入库时"的第一道（那段代码在 adapter 里）；
#   这里是"出网前"的第二道 —— 双保险。故意独立实现（不 import ingress），
#   因为闸门是安全边界，不应该依赖业务模块的实现细节。
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # 手机号：1 开头 11 位（允许中间有空格/短横线）
    ("phone", re.compile(r"(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)"), "[手机号]"),
    # 身份证：18 位（末位可能是 X）
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证]"),
    # 订单号：常见 16~20 位纯数字
    ("order_id", re.compile(r"(?<!\d)\d{16,20}(?!\d)"), "[单号]"),
    # 邮箱
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[邮箱]"),
]


def redact_pii(text: str) -> tuple[str, list[str]]:
    """把文本里的 PII 换成占位符。返回 (脱敏后文本, 命中的类别列表)。

    为什么用占位符而不是删掉整句：
        删掉会破坏语义，模型就看不懂上下文了；占位符保留句式，
        模型仍能判断"这是退款纠纷"还是"这是色差" ✅
    """
    hits: list[str] = []
    out = text
    for name, pat, repl in _PATTERNS:
        new, n = pat.subn(repl, out)
        if n:
            hits.append(name)
            out = new
    return out, hits


# ---------------------------------------------------------------- 闸门
def _audit_cloud(kind: str, text: str, hits: list) -> None:
    """**每次出云写一条 `audit_log`**（路线图 §5.2 验收④）✅

    为什么必须写：
        "出网"是**不可逆**的动作 ✅（数据一旦发出去就收不回 ✅）
        所以每一次都要留痕：**谁、什么类别、带了多少字、脱敏了几处** ✅
    为什么 actor 先写 `system:cloud_guard`：
        现在还没有登录体系 ✅ → 没有"真人"可写 ✅
        **但不能因此就不记** —— 记 `system:cloud_guard` 是诚实的 ✅
        （阶段 4 加了登录后，把 actor 换成 `user:<id>` 即可 ✅）

    ⚠️ 审计失败**不能阻断业务**（一次 fail-open 比"因为写不进日志就发不出去"好）✅
       但会在 stderr 打警告 ✅
    """
    import sys

    try:
        from hub.persistence.db import get_session
        from hub.persistence.models import AuditLog

        with get_session() as s:
            s.add(AuditLog(
                tenant_id="default",
                # 阶段 4 之后换成 user:<id> ✅；现在诚实写"系统闸门"
                actor="system:cloud_guard",
                action="cloud.request",
                resource=f"llm://{kind}",
                detail={"kind": kind, "chars": len(text or ""),
                        "redactions": len(hits or []),
                        "redaction_kinds": sorted({h[0] for h in (hits or [])})},
            ))
            s.commit()
    except Exception as e:                      # noqa: BLE001
        print(f"[cloud_guard] ⚠️ 出云审计写入失败（不阻断业务）：{type(e).__name__}: {e}",
              file=sys.stderr)


def guard(base_url: str, kind: str, text: str, *,
          allow_text: bool = False,
          allow_aggregate: bool = True,
          do_redact: bool = True) -> GuardResult:
    """判断这次请求能不能出网。

    :param base_url:        要打的目标地址（用来判断本地/云端）
    :param kind:            KIND_AGGREGATE / KIND_TEXT / KIND_IMAGE
    :param text:            要发送的文本（图片请求传空串）
    :param allow_text:      是否允许原文上云（配置项 HUB_CLOUD__ALLOW_TEXT）
    :param allow_aggregate: 是否允许聚合数字上云
    :param do_redact:       原文上云前是否做 PII 脱敏
    :raises CloudBlocked:   不允许
    :return: GuardResult（allowed=True 时 text 可能是脱敏后的）
    """
    cloud = is_cloud(base_url)
    if not cloud:
        # 本地：一律放行，不做脱敏（脱敏会改变模型输入，没必要）
        return GuardResult(kind=kind, is_cloud=False, allowed=True,
                           text=text, redactions=[])

    if kind == KIND_IMAGE:
        raise CloudBlocked(
            "图片不允许上云（买家秀可能含人脸/门牌，剥 EXIF 不能解决内容问题）。"
            "图片打标请走本地模型（hub/multimodal/vision.py 也只连本地）。"
        )

    if kind == KIND_TEXT:
        if not allow_text:
            raise CloudBlocked(
                "评价原文不允许上云（HUB_CLOUD__ALLOW_TEXT=false）。"
                "生产环境请让 M1/M3/M5/agent 走本地模型；"
                "只有聚合统计（如 hub brief 的输入）才允许出网。"
            )
        if do_redact:
            new, hits = redact_pii(text)
            _audit_cloud(kind, new, hits)
            return GuardResult(kind=kind, is_cloud=True, allowed=True,
                               text=new, redactions=hits)
        _audit_cloud(kind, text, [])
        return GuardResult(kind=kind, is_cloud=True, allowed=True,
                           text=text, redactions=[])

    if kind == KIND_AGGREGATE:
        if not allow_aggregate:
            raise CloudBlocked("聚合统计也被禁止上云（HUB_CLOUD__ALLOW_AGGREGATE=false）。")
        _audit_cloud(kind, text, [])
        return GuardResult(kind=kind, is_cloud=True, allowed=True,
                           text=text, redactions=[])

    # 未知类别按最严格处理 —— 不允许"因为没写分支而默认放行"
    raise CloudBlocked(f"未知的数据类别 {kind!r}，按最严格处理（拒绝）")
