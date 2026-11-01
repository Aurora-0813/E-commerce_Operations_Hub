# -*- coding: utf-8 -*-
"""
知识条目（knowledge.py）—— 阶段 3.8
==================================
路线图 §9.5.2 要堵的缺口：

    "处置口径**硬编码**在 `proposer.REPLY_REMEDY` 里 ✅
     ——运营想改成『先道歉再解释』，得找开发改代码、重新发版 ✅"

`knowledge_entry.type` 里有 **`prompt_patch`** ✅ → 知识条目可以是一段"提示词补丁"：

    DRAFT →（人审）→ PUBLISHED → 注入 M5/agent 的提示词 ✅
    **业务方发布一条，AI 的处置口径就变，不用改代码** ✅

**注入方式（必须是"可解释的拼接"，路线图原话）**：

```text
把已发布条目按 (topic, type) 取出，拼到提示词的【固定段落】
   ↓
不覆盖系统提示，不替换规则，只"追加口径说明" ✅
提示词 hash 里带上知识条目 version → 变更可追溯 ✅
```

**为什么"不覆盖系统提示"是硬规矩**：
    系统提示里有反幻觉规则（"只能引用 E1..En"）✅
    如果知识条目能覆盖它，业务方一条 patch 就能把防线拆掉 ✅ ✗
    → 所以 patch **只能追加**，且在**独立段落**里 ✅
"""
from __future__ import annotations

import hashlib

TYPE_PATCH = "prompt_patch"


# ════════════════════════════════════════════════════ 读
def active_patches(topic: str | None, *, kind: str = TYPE_PATCH) -> list[dict]:
    """取出**已发布**的知识条目（DRAFT 不生效 ✅）。

    按 `(topic, type)` 过滤：
        `source_ref` 里可以有 `{"topic": "quality"}` ✅
        没有 topic 的条目 = **全局口径**（对所有 topic 生效 ✅）
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import KnowledgeEntry

    with get_session() as s:
        rows = s.scalars(select(KnowledgeEntry).where(
            KnowledgeEntry.type == kind,
            KnowledgeEntry.status == "PUBLISHED",        # ← **DRAFT 不生效** ✅
        ).order_by(KnowledgeEntry.created_at)).all()
        out = []
        for k in rows:
            ref = k.source_ref or {}
            ktopic = ref.get("topic") if isinstance(ref, dict) else None
            if ktopic and topic and ktopic != topic:
                continue                                  # 别的 topic，跳过 ✅
            out.append({"id": str(k.id), "title": k.title, "content": k.content,
                        "version": int(k.version or 1), "topic": ktopic})
        return out


def render_patches(topic: str | None, *, kind: str = TYPE_PATCH) -> tuple[str, str]:
    """渲染成 prompt 里的**独立段落** + 返回 hash（版本可追溯 ✅）。

    :return: (段落文本, hash)
        `段落文本` 为空 = 没有知识条目（调用方就不拼 ✅）
    """
    items = active_patches(topic, kind=kind)
    if not items:
        return "", ""
    lines = ["【口径说明】（业务方发布的知识条目，**只追加，不覆盖上面的规则**）："]
    for i, it in enumerate(items, 1):
        lines.append(f"  K{i}. {it['title']}（v{it['version']}）：{it['content']}")
    text = "\n".join(lines)
    return text, prompt_hash(items)


def prompt_hash(items: list[dict]) -> str:
    """提示词 hash：**带上每条知识条目的 version** ✅

    为什么：口径改了要能回答"这次输出用的是哪一版口径" ✅
    """
    raw = "|".join(f"{it['id']}@{it['version']}" for it in items)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════ 写（人审流程）
def create(title: str, content: str, *, topic: str | None = None,
           kind: str = TYPE_PATCH, tenant_id: str = "default") -> str:
    """新建一条 DRAFT（**默认不生效** ✅ —— 必须人审发布 ✅）。"""
    from hub.persistence.db import get_session
    from hub.persistence.models import KnowledgeEntry

    with get_session() as s:
        k = KnowledgeEntry(tenant_id=tenant_id, type=kind, title=title,
                           content=content,
                           source_ref={"topic": topic} if topic else {},
                           status="DRAFT", version=1)
        s.add(k)
        s.flush()
        kid = str(k.id)
        s.commit()
    return kid


def publish(kid: str) -> dict:
    """发布：DRAFT → PUBLISHED，**version +1** ✅

    ⚠️ 只允许从 DRAFT 发布 ✅（重复发布 = 重复加版本 = hash 乱变 ✅ ✗）
    """
    from uuid import UUID

    from hub.persistence.db import get_session
    from hub.persistence.models import KnowledgeEntry

    with get_session() as s:
        k = s.get(KnowledgeEntry, UUID(str(kid)))
        if k is None:
            raise LookupError(f"知识条目不存在：{kid}")
        if k.status == "PUBLISHED":
            return {"ok": False, "reason": "已经是 PUBLISHED，不重复发布"}
        k.status = "PUBLISHED"
        k.version = int(k.version or 1) + 1
        v = k.version
        s.commit()
    return {"ok": True, "version": v}
