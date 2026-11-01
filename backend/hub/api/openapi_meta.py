# -*- coding: utf-8 -*-
"""
OpenAPI 元数据（openapi_meta.py）—— 让 `/api/docs` 变成"能读的文档"
===================================================================
**为什么需要这个文件**（2026-11-01）

    `/api/docs` 原来长这样：
        GET  /api/today      `Get Today`      ← 英文，而且是**函数名自动生成**的
        GET  /api/notify     `List Notify`    ← 中文项目里出现英文标题
        分组是 read / write / charts          ← **没有一句「这组是干什么的」**
        返回结构 **看不到** ✅ ✗               ← 前端不知道会拿到什么

**为什么用「后处理器」而不是逐个改 43 个装饰器**

    ① 43 个接口 × (summary + tags + responses) = 大量重复编辑，容易漏 ✅
    ② 集中在一张表里，**改文案不用碰业务代码** ✅
    ③ 以后加接口，只要在这里补一行 ✅
    ④ **不侵入路由定义**（`read.py` / `charts.py` 保持干净 ✅）

**代价（必须说清）**

    · 这里加的 `example` 是**示例**，不是 `response_model`
    · → **不会校验真实返回**（返回少了字段也不会报错 ✅ ✗）
    · 真正的 `response_model` 要写在装饰器上；本文件**先解决「看得见」** ✅

**编码约定**：本文件所有中文文案里的引号一律用 `「」`，**禁止 ASCII 双引号**
    —— 2026-11-01 我在这里犯了两次同样的错（字符串被提前闭合 ✅）
"""
from __future__ import annotations

from typing import Any

# ══════════════════════════════════════════════════════════════
#  ① 分组（tag）说明 —— Swagger 里每组上面那行灰字
# ══════════════════════════════════════════════════════════════
TAGS_META = [
    {"name": "认证", "description":
        "登录 / 登出 / 我是谁。**登出是服务端撤销**（jti 进 Redis 黑名单，立即失效）✅"},
    {"name": "读接口 · 工作台", "description":
        "运营每天第一眼看的：今天该做什么、数据现状、系统健康、AI 日报、门禁结果。"
        "**都是只读，全部走和 CLI 同一个数据源** ✅"},
    {"name": "读接口 · 明细", "description":
        "提案列表/详情（详情里带**历史战绩**）、操作日志、通知、知识条目。"},
    {"name": "图表 · 给前端画图", "description":
        "13 张图的聚合数据。**数据全部来自真实表**；⑧ 四因子只给库里的真实输入 + note，"
        "**不编造归一化因子** ✅"},
    {"name": "提交 · 异步任务", "description":
        "**202 不是完成** ✅ 提交后要轮询 GET /api/runs/{id}；"
        "agent_run_id 在 params 里（DONE 后才有）。"},
    {"name": "审批 · 人的决定", "description":
        "**只有 supervisor 能批/拒** ✅；批准要 confirm: true（危险操作二次确认）；"
        "**拒绝必须带原因**（空原因 422）。"},
    {"name": "运维 · 慎用", "description":
        "通知标记已读、知识条目建/发布、xlsx 导出、**死信重放（危险）**。"},
]

# ══════════════════════════════════════════════════════════════
#  ② 每个接口：中文标题 / 分组 / 说明 / 响应示例
# ══════════════════════════════════════════════════════════════
META: dict[tuple[str, str], dict[str, Any]] = {
    # ---------------- 认证 ----------------
    ("GET", "/api/ping"): dict(
        summary="探活", tag="认证",
        description="**免鉴权**。只返回服务在不在，不查数据库。",
        example={"ok": True, "service": "e-commerce-operations-hub"}),
    ("POST", "/api/auth/login"): dict(
        summary="登录（换 JWT）", tag="认证",
        description="邮箱 + 密码 → access_token（默认 **3 小时**）。\n\n"
                    "**失败统一话术**「邮箱或密码不对」（防账号枚举）；"
                    "用户不存在时**也跑一次 bcrypt** 做时间对齐，"
                    "避免通过耗时判断账号是否存在 ✅",
        example={"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer",
                 "expires_in": 10800,
                 "user": {"id": "uuid", "email": "operator@hub.local",
                          "display_name": "运营", "role": "operator"}}),
    ("POST", "/api/auth/logout"): dict(
        summary="登出（服务端撤销）", tag="认证",
        description="把这个 token 的 jti 写进 Redis 黑名单，TTL = 剩余有效期。"
                    "**只作废当前 token**，不影响其他设备 ✅\n\n"
                    "Redis 连不上 → **503**（不假装成功 ✅）",
        example={"ok": True, "message": "已登出（该 token 立即失效）"}),
    ("GET", "/api/auth/me"): dict(
        summary="我是谁", tag="认证",
        description="用 token 换身份（前端刷新页面后恢复登录态）。",
        example={"id": "uuid", "email": "operator@hub.local",
                 "display_name": "运营", "role": "operator"}),

    # ---------------- 读 · 工作台 ----------------
    ("GET", "/api/today"): dict(
        summary="今日工作台", tag="读接口 · 工作台",
        description="按 P 档 + 是否到期分组：**🔴 要我做**（待批/到期复盘/死信）/ "
                    "**🔵 知会**（等观察窗口/已出结论）。和 hub today **同一个数据源** ✅",
        example={"must_do": [{"kind": "hitl", "title": "5 个提案待批",
                              "hint": "最早的一件等了 2 天"}],
                 "waiting": [{"kind": "observe", "title": "6 个动作在等观察窗口",
                              "hint": "最早 2026-11-13 出结果"}],
                 "anomalies": []}),
    ("GET", "/api/status"): dict(
        summary="业务数据现状（13 项）", tag="读接口 · 工作台",
        description="库里到底有多少东西：评价数 / 降噪率 / 簇数 / 差评数 / 提案数 / 结论分布……",
        example={"records": 6950, "kept": 4064, "clusters": 22,
                 "negative_reviews": 973, "proposals": 19}),
    ("GET", "/api/health"): dict(
        summary="系统健康（7 项）", tag="读接口 · 工作台",
        description="最近一次运行的**健康度**（不是实时探活 ✅）：数据新鲜度 / 失败率 / "
                    "积压 / 死信 / 门禁 / 模型可用性 / Redis",
        example={"ok": True, "checks": [{"name": "redis", "ok": True,
                                         "detail": "PING ok"}]}),
    ("GET", "/api/brief"): dict(
        summary="AI 日报", tag="读接口 · 工作台",
        description="3~5 条人话 + 来源指标。**没生成过 → available: false**"
                    "（不返回空壳 ✅）。\n\n"
                    "每个数字都经过**校验**：必须能在输入指标里找到，找不到就丢那一条 ✅",
        example={"available": True, "as_of": "2026-11-01",
                 "bullets": ["今天有 5 个提案待批，其中 2 个是 P0"],
                 "inputs": {"待批": 5}}),
    ("GET", "/api/eval/latest"): dict(
        summary="最近一次门禁", tag="读接口 · 工作台",
        description="19 条硬门禁 + 1 条软门禁的通过情况。",
        example={"run_id": "eval-20261101-0915-4821", "passed": 19, "total": 19,
                 "soft_passed": 1, "exit_code": 0}),

    # ---------------- 读 · 明细 ----------------
    ("GET", "/api/proposals"): dict(
        summary="提案列表（分页 + 筛选）", tag="读接口 · 明细",
        description="**只返回摘要**，详情走 /proposals/{id} ✅\n\n"
                    "筛选：status / topic / limit / offset",
        example={"total": 19, "limit": 20, "offset": 0,
                 "items": [{"id": "uuid", "tool_name": "review.draft_reply",
                            "risk_level": "L1", "status": "PROPOSED",
                            "topic": "quality", "created_at": "2026-11-01T09:00:00"}]}),
    ("GET", "/api/proposals/{proposal_id}"): dict(
        summary="提案详情（带历史战绩）", tag="读接口 · 明细",
        description="三块：**内容**（tool/args/rationale）、**依据**（evidence_refs）、"
                    "**历史战绩**（experience：这个 topic+tool 过去有效/有害几次）✅",
        example={"id": "uuid", "tool_name": "review.draft_reply",
                 "args": {"cluster_id": "uuid", "content": "..."},
                 "rationale": "为避免更多差评，需及时回应掉色问题",
                 "evidence_refs": [{"issue_cluster": "uuid"}],
                 "experience": {"positive": 3, "negative": 1, "total": 4}}),
    ("GET", "/api/audit"): dict(
        summary="操作日志（分页 + 筛选）", tag="读接口 · 明细",
        description="**8 类动作在业务层写** → CLI 和 API 都覆盖 ✅。"
                    "筛选：action / actor / limit / offset",
        example={"total": 42, "limit": 20, "offset": 0,
                 "items": [{"id": 1, "at": "2026-11-01T09:00:00",
                            "actor": "user:uuid", "action": "proposal.approve",
                            "resource": "proposal:uuid", "ip": "127.0.0.1"}]}),
    ("GET", "/api/notify"): dict(
        summary="通知列表（含未读数）", tag="读接口 · 明细",
        description="unread = **非 READ 的条数** → 前端铃铛上的数字就是它 ✅\n\n"
                    "⚠️ 通知**不会自己产生**：要先跑 hub notify --scan"
                    "（部署时接 cron ✅）",
        example={"total": 3, "unread": 2, "limit": 50, "offset": 0,
                 "items": [{"id": 115, "event_type": "dead_letter",
                            "status": "PENDING", "attempts": 0}]}),
    ("GET", "/api/knowledge"): dict(
        summary="知识条目列表（含是否生效）", tag="读接口 · 明细",
        description="每条返回 effective：DRAFT 是 false / PUBLISHED 是 true ✅\n\n"
                    "**为什么要它**：运营最关心「我发的这条现在有没有在影响 AI」",
        example={"total": 1, "items": [{"id": "uuid", "type": "prompt_patch",
                                        "title": "质量口径", "status": "PUBLISHED",
                                        "version": 2, "effective": True,
                                        "effective_note": "已注入提示词 ✅"}]}),

    # ---------------- 图表 ----------------
    ("GET", "/api/charts/cluster-tiers"): dict(
        summary="① P 档环形", tag="图表 · 给前端画图",
        description="P0/P1/P2/P3 各几个簇（**加起来 = 库里总数** ✅）",
        example=[{"tier": "P0", "n": 2}, {"tier": "P1", "n": 5},
                 {"tier": "P2", "n": 8}, {"tier": "P3", "n": 7}]),
    ("GET", "/api/charts/clusters"): dict(
        summary="⑦ 簇条数（表格同源）", tag="图表 · 给前端画图",
        description="22 个簇的条数 / P 档 / 分数。sort 可取 reviews / priority / label",
        example=[{"id": "uuid", "label": "掉色严重", "reviews": 123,
                  "tier": "P0", "score": 0.87}]),
    ("GET", "/api/charts/hitl-trend"): dict(
        summary="② 待批趋势", tag="图表 · 给前端画图",
        description="按天统计 hitl_task 的创建数 / 待批数。",
        example=[{"date": "2026-10-30", "created": 3, "pending": 2}]),
    ("GET", "/api/charts/bad-rate-trend"): dict(
        summary="⑨ 差评率趋势", tag="图表 · 给前端画图",
        description="按天：差评数 / 总数 / 差评率（**有测试保证 bad 不超过 total** ✅）",
        example=[{"date": "2026-10-30", "bad": 12, "total": 40, "rate": 0.3}]),
    ("GET", "/api/charts/observe-timeline"): dict(
        summary="③ 等窗口甘特", tag="图表 · 给前端画图",
        description="每个动作的观察窗口（baseline_end → observe_end）。"
                    "**observe_end 是 DATE** ✅（不是 datetime）",
        example=[{"proposal_id": "uuid", "start": "2026-10-25",
                  "end": "2026-11-13", "status": "WAITING"}]),
    ("GET", "/api/retro"): dict(
        summary="⑫⑬⑭ 复盘报告（DID）", tag="图表 · 给前端画图",
        description="4 组 DID + **bootstrap 置信区间** + 结论。\n\n"
                    "⚠️ **区间跨 0 = 不能下结论** ✅ —— 页面上要能一眼看出来",
        example=[{"proposal_id": "uuid", "conclusion": "POSITIVE",
                  "effect": -0.1071, "ci_low": -0.18, "ci_high": -0.03,
                  "reason_code": "SIGNIFICANT"}]),
    ("GET", "/api/audit/trend"): dict(
        summary="⑱ 日志趋势", tag="图表 · 给前端画图",
        description="按天 + 按 action 分组统计审计条数。",
        example=[{"date": "2026-10-30", "action": "proposal.approve", "n": 3}]),
    ("GET", "/api/charts/topic-distribution"): dict(
        summary="⑥ 问题类型饼", tag="图表 · 给前端画图",
        description="6 类问题的簇数（从 cluster_member → review_topic 取众数 ✅）",
        example=[{"topic": "quality", "n": 8}, {"topic": "size", "n": 5}]),
    ("GET", "/api/charts/cluster-trend"): dict(
        summary="④ 簇周趋势", tag="图表 · 给前端画图",
        description="某个簇按**周**的评价数（用 record.review_time 分周 ✅）",
        example=[{"week": "2026-W43", "n": 12}, {"week": "2026-W44", "n": 19}]),
    ("GET", "/api/charts/gmv-top"): dict(
        summary="⑪ GMV TopN", tag="图表 · 给前端画图",
        description="按簇汇总最近 N 天的 GMV（sales_daily 求和 ✅）",
        example=[{"label": "掉色严重", "gmv": 68675000.0, "tier": "P0"}]),
    ("GET", "/api/charts/price-vs-badrate"): dict(
        summary="⑩ 价格散点", tag="图表 · 给前端画图",
        description="数据来自 anomaly.evidence.prices 和 review_rates"
                    "（**真实值，不是编的** ✅）",
        example=[{"spu": "TB-0002", "price": 259.0, "bad_rate": 0.31}]),
    ("GET", "/api/clusters/{cluster_id}/factors"): dict(
        summary="⑧ 簇四因子（只给真实输入）", tag="图表 · 给前端画图",
        description="返回**库里的原始输入** + note。\n\n"
                    "**故意不返回「归一化后的 0~1 因子」** ✅ —— 那需要一个人为公式，"
                    "而我们没有依据；编出来会让雷达图看起来专业但没有意义 ✅",
        example={"cluster_id": "uuid", "inputs": {"reviews": 123, "tier": "P0",
                                                  "growth_rate": 0.4, "spus": 5},
                 "note": "未归一化：这些是库里的原始输入"}),
    ("GET", "/api/handle/{run_id}"): dict(
        summary="⑮ agent 轨迹与结论", tag="图表 · 给前端画图",
        description="agent_run：4 步轨迹 / 每步工具与参数 / 结论 / Critic 意见 / 状态",
        example={"id": "uuid", "status": "DONE",
                 "steps": [{"idx": 1, "tool": "query_cluster"}],
                 "result": {"tool": "review.draft_reply", "confidence": 0.8}}),
    ("GET", "/api/handle/{run_id}/trace"): dict(
        summary="⑯ 每步 token 与耗时", tag="图表 · 给前端画图",
        description="trace_span 的每段。**时长 = end_ts - start_ts** ✅"
                    "（表里没有 duration 列）",
        example=[{"name": "llm.judge", "in_tokens": 689, "out_tokens": 57,
                  "ms": 3200}]),

    # ---------------- 提交 ----------------
    ("POST", "/api/runs"): dict(
        summary="提交全链（202）", tag="提交 · 异步任务",
        description="入 Redis Stream，**不阻塞** ✅。\n\n"
                    "· 重复提交 → **409 + 已有 run_id**（前端可直接跳去轮询）\n"
                    "· Redis 连不上 → **503**（链已写库，等 worker 对账会捡起来 ✅）",
        example={"run_id": "uuid", "trace_id": "hex", "status": "QUEUED",
                 "message": "已排队；用 GET /api/runs/{id} 查进度"}),
    ("GET", "/api/runs/{run_id}"): dict(
        summary="查任务状态", tag="提交 · 异步任务",
        description="status / current_stage / error / **agent_run_id**"
                    "（agent 任务跑完后在 params 里 ✅）",
        example={"run_id": "uuid", "kind": "handle:2adf0897", "status": "DONE",
                 "current_stage": None, "agent_run_id": "uuid"}),
    ("POST", "/api/handle"): dict(
        summary="提交 agent 任务（202）", tag="提交 · 异步任务",
        description="cluster_id 支持 **UUID 或前 8 位前缀** ✅\n\n"
                    "**同簇重复提交 → 409 + 同一个 run_id**（这是幂等，不是报错 ✅）",
        example={"run_id": "uuid", "cluster_id": "uuid", "status": "QUEUED"}),

    # ---------------- 审批 ----------------
    ("POST", "/api/proposals/{proposal_id}/approve"): dict(
        summary="批准（supervisor + confirm）", tag="审批 · 人的决定",
        description="L2 会**真的执行**；L3 仍然转人工 ✅\n\n"
                    "· 角色不够 → **403**\n"
                    "· 没带 confirm: true → **400**\n"
                    "· 没有待批任务（已批过/已拒）→ **404**\n"
                    "· by 记成 **user:uuid**（阶段 4 有登录了，不再手填角色名 ✅）",
        example={"ok": True, "task_id": "uuid",
                 "result": {"decision": "EXECUTED"}}),
    ("POST", "/api/proposals/{proposal_id}/reject"): dict(
        summary="拒绝（必须带原因）", tag="审批 · 人的决定",
        description="reason 为空 → **422**（pydantic min_length=1 ✅）\n\n"
                    "路线图三条硬规矩之一：**拒绝必须带原因** ✅",
        example={"ok": True, "task_id": "uuid", "reason": "成本不合适"}),

    # ---------------- 运维 ----------------
    ("POST", "/api/propose"): dict(
        summary="为某簇生成提案", tag="运维 · 慎用",
        description="传 cluster_id → **只处理这个簇** ✅"
                    "（only_cluster，2026-11-01 修的）\n\n不传 → 批处理（原来的行为 ✅）",
        example={"clusters": 1, "proposals": 1, "ids": ["uuid"]}),
    ("POST", "/api/retro"): dict(
        summary="重新复盘", tag="运维 · 慎用",
        description="only_due=true 时**不调 LLM**（跑得很快 ✅）",
        example={"ok": True, "done": 4, "skipped": 14}),
    ("POST", "/api/handle/{run_id}/adopt"): dict(
        summary="采纳 agent 结论 → 生成提案", tag="运维 · 慎用",
        description="从 agent_run **重建** HandleResult（**不重跑 agent** ✅）；"
                    "**参数由代码构造，不让模型填** ✅\n\n"
                    "· 非 DONE → **409**\n· 已采纳过 → 返回原 proposal_id（幂等 ✅）",
        example={"ok": True, "proposal_id": "uuid", "already_adopted": False}),
    ("POST", "/api/handle/{run_id}/reject"): dict(
        summary="不采纳（带原因）", tag="运维 · 慎用",
        description="**只写审计**，不改 agent_run 状态 ✅（保留证据）",
        example={"ok": True, "run_id": "uuid", "reason": "建议不合适"}),
    ("POST", "/api/notify/{nid}/read"): dict(
        summary="标记通知已读", tag="运维 · 慎用",
        description="status 改成 READ。\n\n"
                    "⚠️ notification 表**没有 read_at 列** ✅ → 用 status 表达"
                    "（不新建列 ✅）",
        example={"ok": True, "id": 115, "was": "PENDING", "now": "READ"}),
    ("POST", "/api/knowledge"): dict(
        summary="新建知识条目（DRAFT）", tag="运维 · 慎用",
        description="**DRAFT 不生效** ✅ → 要 /publish 才会注入提示词。",
        example={"ok": True, "id": "uuid", "status": "DRAFT"}),
    ("POST", "/api/knowledge/{kid}/publish"): dict(
        summary="发布知识条目（supervisor）", tag="运维 · 慎用",
        description="PUBLISHED + version+1 → **立即注入提示词** ✅\n\n"
                    "· prompt_patch **只能追加、不能覆盖**系统提示 ✅"
                    "（否则能拆掉反幻觉防线）\n· 重复发布 → **409**",
        example={"ok": True, "id": "uuid", "status": "PUBLISHED", "version": 2}),
    ("GET", "/api/export/{kind}"): dict(
        summary="导出 xlsx（reviews / clusters / retro）", tag="运维 · 慎用",
        description="返回**文件流**。\n\n"
                    "· **PII 已复查脱敏** ✅（首行写明「已脱敏 N 处」）\n"
                    "· 响应头带 X-Export-Rows / X-Export-Pii-Redacted\n"
                    "· 上限 **1 万行**（超了 400，提示分页 ✅）",
        example=["（二进制 xlsx；响应头见 X-Export-*）"]),
    ("POST", "/api/dlq/replay"): dict(
        summary="重放死信（危险）", tag="运维 · 慎用",
        description="**supervisor + confirm: true** 才能做 ✅\n\n"
                    "重放 = 把已经失败的东西再跑一遍，可能重复写数据 ✅",
        example={"ok": True, "exit_code": 0, "message": "已重新入队"}),
    ("POST", "/api/ingest"): dict(
        summary="上传平台导出文件（xlsx）", tag="运维 · 慎用",
        description="**multipart 上传 → 解析 → 落库** ✅（2026-11-01 补的真缺口："
                    "原来只能 hub p0 命令行导）\n\n"
                    "· `platform` 是 taobao / jd，**必须和文件对得上**\n"
                    "· `data_type` 是 review / product / sales\n"
                    "· 文件 ≤ **20MB**（超了 413）\n"
                    "· 同一个文件（SHA256）再传 → **existed: true + 原 batch_id**"
                    "（幂等 ✅ 不是报错）\n\n"
                    "⚠️ **平台传错**（jd 文件当 taobao）→ rows_ok=0 / rows_bad 全坏，"
                    "**但仍然 201** —— 看 warnings 里的人话原因 ✅",
        example={"ok": True, "batch_id": "uuid", "existed": False,
                 "file": "jd_sales_202610.xlsx", "bytes": 96770,
                 "platform": "jd", "data_type": "sales",
                 "rows_ok": 2250, "rows_bad": 0, "warnings": [],
                 "message": "已入库 2250 条 ✅"}),
}


# ══════════════════════════════════════════════════════════════
#  ③ 后处理：把上面那张表贴进 OpenAPI
# ══════════════════════════════════════════════════════════════
def apply_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """就地修改 OpenAPI schema（由 `main.create_app()` 挂到 `app.openapi` 上）。

    **只改文档，不影响任何请求行为** ✅
    """
    schema["tags"] = TAGS_META

    for path, ops in schema.get("paths", {}).items():
        for verb, op in ops.items():
            meta = META.get((verb.upper(), path))
            if meta is None:
                continue
            op["summary"] = meta["summary"]
            if meta.get("description"):
                op["description"] = meta["description"]
            if meta.get("tag"):
                op["tags"] = [meta["tag"]]
            # **响应示例**：让 Swagger 里出现 Example Value ✅
            # ⚠️ 这是**示例**，不是 response_model —— 不会校验真实返回 ✅
            ex = meta.get("example")
            if ex is not None:
                resp = op.setdefault("responses", {}).setdefault("200", {})
                resp.setdefault("content", {}).setdefault(
                    "application/json", {})["example"] = ex
    return schema
