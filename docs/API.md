# API 文档（`docs/API.md`）

> **运行时文档**：启动 `uvicorn hub.api.main:app` 后打开 **http://127.0.0.1:8000/api/docs**
> （FastAPI 自带的 Swagger，能直接点着调 ✅）
>
> **本文档补的是 Swagger 里看不到的**：为什么这么设计、错误码含义、哪些接口是危险的。
>
> 基线：2026-11-01 ｜ **43 条路由** ｜ 全部要鉴权（除了 `/ping` 和 `/auth/login`）

---

## 0. 通用约定

### 0.1 认证

```http
Authorization: Bearer <jwt>
```

- 登录拿 token：`POST /api/auth/login` → **有效期 3 小时**（`.env` 可配）
- **服务端登出**：`POST /api/auth/logout` → jti 进 Redis 黑名单 → **立刻失效** ✅
- 只作废**当前这个 token**，不影响其他设备 ✅

### 0.2 错误格式（**全部带 trace_id**）

```json
{ "code": "ROLE_TOO_LOW", "message": "需要 supervisor 及以上角色，你是 operator", "trace_id": "api-xxx" }
```

| 状态码 | code | 什么时候 |
| --- | --- | --- |
| **401** | `NO_TOKEN` / `TOKEN_INVALID` | 没带 token / token 过期或签名不对 / 已登出 |
| **403** | `ROLE_TOO_LOW` | 角色不够（**API 层与工具层同一套角色序** ✅） |
| **400** | `CONFIRM_REQUIRED` | 危险操作没带 `confirm: true` |
| | `BAD_ID` / `BAD_KIND` | 参数格式不对 |
| **404** | `NOT_FOUND` / `CLUSTER_NOT_FOUND` | 资源不存在 |
| **409** | `ALREADY_RUNNING` | 已有同 kind 的活跃任务（**带已有 run_id** ✅） |
| | `ALREADY_PUBLISHED` / `NOT_DONE` | 重复发布 / agent 还没跑完 |
| **422** | （pydantic 默认） | 字段校验失败，如**拒绝必须带原因** |
| **503** | `QUEUE_UNAVAILABLE` / `REVOKE_FAILED` | Redis 连不上（**不谎报成功** ✅） |

### 0.3 角色

```text
system < analyst < operator < supervisor < admin
   ↓
API 层用 `CallContext.has_role`（**和工具层同一套** ✅）
```

**只有 3 类接口要求 supervisor**：`approve` / `reject` / `knowledge publish` / `dlq replay`

### 0.4 危险操作（**必须 `confirm: true`**）

`POST /api/proposals/{id}/approve` · `POST /api/dlq/replay`

---

## 1. 认证（4）

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | — | `{email, password}` → `{access_token, expires_in, user}`<br>**失败统一话术**"邮箱或密码不对"（**防枚举** ✅；不存在的用户也跑一次 bcrypt 做时间对齐 ✅） |
| POST | `/api/auth/logout` | 任意 | 撤销当前 token；Redis 挂 → **503**（不假装成功） |
| GET | `/api/auth/me` | 任意 | 用 token 换身份（前端刷新页面用） |
| GET | `/api/ping` | — | 免鉴权探活 |

---

## 2. 读接口（10）

| 路径 | 说明 | 数据源 |
| --- | --- | --- |
| `GET /api/today` | 🔴 要我做 / 🔵 知会 | `interact.today.collect()`（**和 `hub today` 同源** ✅） |
| `GET /api/status` | 业务数据现状（13 项） | `observability.status.collect()` |
| `GET /api/health` | 最近一次运行健康度（7 项） | `observability.health.collect()` |
| `GET /api/brief` | AI 日报；**没生成过 → `available:false`** | `brief.latest()` |
| `GET /api/eval/latest` | 最近一次门禁 | `eval.store.history()` |
| `GET /api/proposals` | 分页 + `status`/`topic` 筛选 | `action_proposal` |
| `GET /api/proposals/{id}` | 详情 + **`experience`（图⑤ 历史战绩）** | `action_proposal` + `experience` |
| `GET /api/audit` | 分页 + `action`/`actor` 筛选 | `audit_log` |
| `GET /api/notify` | **`total` / `unread` / `items`** | `notification`（`unread` = 非 `READ` ✅） |
| `GET /api/knowledge` | **`effective` 字段**（DRAFT=false / PUBLISHED=true） | `knowledge_entry` |

---

## 3. 图表 / 轨迹（14）

| 路径 | 图 | 备注 |
| --- | --- | --- |
| `GET /api/charts/cluster-tiers` | ① P 档环形 | 簇数之和 = 库里总数 ✅ |
| `GET /api/charts/hitl-trend?days=` | ② 待批趋势 | 按 `hitl_task.created_at` |
| `GET /api/charts/observe-timeline` | ③ 等窗口甘特 | `observe_end` 是右端 |
| `GET /api/charts/cluster-trend?cluster_id=` | ④ 簇周趋势 | 按 `record.review_time` 分周 |
| `GET /api/charts/topic-distribution` | ⑥ 问题类型饼 | `cluster_member → review_topic` |
| `GET /api/charts/clusters?sort=` | ⑦ 簇条数（表格同源） | `reviews` / `priority` / `label` |
| `GET /api/clusters/{id}/factors` | ⑧ 四因子雷达 | **只给库里的真实输入 + `note`**；不编造归一化因子 ✅ |
| `GET /api/charts/bad-rate-trend?platform=&days=` | ⑨ 差评率趋势 | `bad ≤ total` 有测试保证 ✅ |
| `GET /api/charts/price-vs-badrate` | ⑩ 价格散点 | 来自 `anomaly.evidence.prices` + `review_rates` |
| `GET /api/charts/gmv-top?n=` | ⑪ GMV TopN | `sales_daily` 最近 N 天求和 |
| `GET /api/retro` | ⑫⑬⑭ DID | `action_outcome`（含 `effect` / `conclusion`） |
| `GET /api/handle/{id}` | ⑮ agent 轨迹 | `agent_run` |
| `GET /api/handle/{id}/trace` | ⑯ 每步 token | `trace_span`（**时长 = `end_ts - start_ts`** ✅ 没有 duration 列） |
| `GET /api/audit/trend?days=` | ⑱ 日志趋势 | `audit_log` 按天 + action 分组 |

**⑰ 日志表** = `GET /api/audit`（在 §2）✅ ｜ **⑲⑳** = `/health` `/eval/latest` ✅

---

## 4. 写接口（15）

### 4.1 提交（异步，**202**）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/runs` | `{kind:"full"}` → **202 + run_id**；重复 → **409 + 已有 run_id**；Redis 断 → 503 |
| POST | `/api/handle` | `{cluster_id}`（UUID 或前 8 位）→ **202 + run_id**；**同簇 409 幂等** |
| GET | `/api/runs/{id}` | 状态；**`agent_run_id` 在 `params` 里**（DONE 后才有 ✅） |

**为什么 202 里的 id 是 `pipeline_run.id`（任务）而不是 `agent_run.id`（结果）**：
任务会失败/重试/死信，结果只有一份 → **两者是不同生命周期** ✅

### 4.2 审批（**supervisor**）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/proposals/{id}/approve` | **`confirm:true` 必需**；L2 会真执行，L3 仍转人工；`by=user:<uuid>` |
| POST | `/api/proposals/{id}/reject` | **空原因 → 422**（`min_length=1`） |

> ⚠️ URL 用 **`proposal_id`**（不是 `hitl_task_id`）—— 前端从提案列表点进来手上只有 proposal_id ✅
> 接口内部按 `proposal_id` 找 PENDING 待办 ✅

### 4.3 agent 采纳

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/handle/{id}/adopt` | 从 `agent_run` **重建** `HandleResult`（**不重跑 agent** ✅）；已采纳过 → 返回原 `proposal_id`；非 DONE → **409** |
| POST | `/api/handle/{id}/reject` | 带原因，**只写审计**，不改状态 |

### 4.4 其他

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/propose?cluster_id=` | 为某簇生成提案（**真按簇过滤** ✅ `only_cluster`） |
| POST | `/api/retro` | 重新复盘（`only_due=true` 时不调 LLM ✅） |
| POST | `/api/notify/{id}/read` | `status="READ"`（表**没有** `read_at` 列 ✅） |
| POST | `/api/knowledge` | **201 + DRAFT**（**不生效** ✅） |
| POST | `/api/knowledge/{id}/publish` | **supervisor**；PUBLISHED + version+1 → **立即注入提示词** ✅ |
| GET | `/api/export/{kind}` | `reviews` / `clusters` / `retro` → xlsx 流；头里带 `X-Export-Rows` / `X-Export-Pii-Redacted` |
| POST | `/api/dlq/replay` | **supervisor + `confirm:true`**；重放死信（**危险** ✅） |

---

## 5. 一个完整的调用示例（前端最常走的）

```text
1. POST /api/auth/login      {email, password}       → access_token（1 天 3 小时有效）
2. GET  /api/today           Bearer <token>          → 🔴 要我做：3 件
3. GET  /api/proposals?status=PENDING_APPROVAL       → 找到那 3 件的 id
4. GET  /api/proposals/{id}                          → 看 args / rationale / 依据原文
5. POST /api/proposals/{id}/approve  {confirm:true}  → supervisor 才能过
6. POST /api/handle          {cluster_id}            → 202 + run_id
7. GET  /api/runs/{run_id}                            → 轮询到 DONE
8. GET  /api/handle/{agent_run_id}                    → 看 4 步轨迹 + 结论
9. POST /api/handle/{id}/adopt {confirm:true}         → 生成提案
```

---

## 6. 前端要注意的三件事

```text
① **401 就跳登录**（token 3 小时会过期 ✅）
② **operator 不该看到"批准"按钮**（UI 层隐藏 ✅）
   —— 但**这不是安全措施**：API 会 403 ✅（UI 只是少让人点错 ✅）
③ **`202` 不是"完成"** → 要轮询 `GET /api/runs/{id}` ✅
```

---

## 7. 与 Swagger 的分工

| 要看什么 | 去哪 |
| --- | --- |
| 字段级 schema、在线调试 | **`/api/docs`** ✅ |
| **为什么这么设计、错误码含义、哪些危险** | **本文档** ✅ |
