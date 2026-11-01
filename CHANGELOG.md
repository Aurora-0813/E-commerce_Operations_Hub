# CHANGELOG

> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
> 版本号跟**阶段**走（不是语义化版本）—— 因为这个项目是**按阶段交付**的。
> 每条都能在 `git log` 里找到对应提交。

---

## [未发布] —— 阶段 6 / 7

### 计划中
- **阶段 6 部署**：`pyproject.toml` + `setup.ps1` + `daily.ps1` + `docs/部署.md` +
  **cron `*/5 * * * * hub notify --scan --dispatch`**
- **阶段 7 前端**：10 页（Vue3 + Element Plus + ECharts），43 个接口已就绪
- **3.5 的 `precision@3`**：等 2026-11-13 的真实复盘数据

---

## [0.2.0] —— 2026-11-01：阶段 5（写接口 15 个）

### 新增
- **写接口 15 个**：`/runs`（202/409/503）、`/handle`（202，同簇幂等）、
  `/proposals/{id}/approve`（supervisor + confirm）、`/reject`（空原因 422）、
  `/propose?cluster_id=`（**真按簇过滤**）、`/handle/{id}/adopt`、`/reject`、
  `/retro`、`/notify/{id}/read`、`/knowledge`、`/knowledge/{id}/publish`、
  `/export/{kind}`、`/dlq/replay`（supervisor + confirm）
- **补 2 个读接口**：`GET /api/notify`（含 `unread`）、`GET /api/knowledge`（含 `effective`）
- **`hub notify` CLI**：`--scan` / `--dispatch` / `--list`
  —— 修掉"`scan_and_emit()` / `dispatch()` 只有测试在调"的缺口 ✅
- 新增测试：`check_api_write_run` / `write_action` / `write_misc` / `read_extra`

### 修改
- **队列 `kind` 改造**：`handle:<cluster_id[:8]>` —— 因为 `pipeline_run` 的
  `UNIQUE(kind) WHERE 活跃` 会把第二个 agent 任务拒掉；带簇 id 后**同簇幂等、不同簇并行**
- **`proposer.propose()` 加 `only_cluster`** 参数
- **`adopt()` 去重**：按 `(tool, 未完成状态, evidence_refs 含该簇)` 查重
- **`GState` 补 `_llm` 字段**（LangGraph 的 state 合并会丢未声明的键）
- **`hub/api/deps.py`**：把 `AuthUser` / `_trace_id` / `get_current_user` / `require_role`
  从 `main.py` 抽出来 → **修掉循环导入**
- **文档收口**：`实现状态.md` / `改进路线图.md` / `债台账.md` / `.env.example`

### 修复
- `POST /api/propose?cluster_id=` 之前**只回显、没过滤**（调的是批处理）
- `check_classify` 的 stale import（`_parse_array` → `_parse_lines`，M3 输出已改行格式）
- `check_execute` / `check_tools` 的 `role="manager"`（角色序里没有 manager）
- `notify.dispatch()` 的 `skipped` 计数没记账
- 文档里 8 处指向已删文件的引用

### 删除（文档收敛，30 → 28 份）
- `docs/开发思路.md`（2452 行，和 `开发文档` 重叠 > 70%）
- `docs/后端开发实施手册.md`（930 行，和 `开发指南/` 重叠，且"分步实现"已全部做完）
- `docs/开发指南/00_开发路线图.md`（和 `改进路线图.md` 重叠）
- `commit_msg.txt`（临时提交说明）

### 重命名（编号/标题错误的）
- `06_M3_多步归因链_LangGraph.md` → **`06_M3分类.md`**（M3 不是 LangGraph ✅）
- `08_M5网关_M6执行链.md` → **`08_M7网关与执行链.md`**（真实 M5 = 提案、M7 = 执行）
- `12_部署上线运维.md` → **`17_部署上线运维.md`**（`12_` 编号重复）

---

## [0.1.0] —— 2026-10-31：阶段 0~4

### 新增
- **阶段 0 还债**：删 10 个零 import 依赖；契约对齐（删 `contracts/{gateway,retro}.py`）；
  新增 `docs/实现状态.md`（把"设计里写了"和"代码里真有"分开）
- **阶段 1**：OpenAI 兼容客户端（换模型只改 `.env` 三行）；
  **`CloudGuard`**（图片永不出网 / 原文默认不出网 / 聚合可上云 + PII 脱敏）；
  规划能力实测（**`seed` 才是硬约束；`temperature=0` 不保证**）
- **阶段 2**：eval 落库 + `--history`；测试分 quick/full/probe 三组；
  `snapshot.py --check`；`RunStats.module` 必填
- **阶段 3**：五个只读工具（`idempotent=False` ✅）；**MCP server**（薄壳走网关）；
  **LangGraph 对照实现**（22/22 行为等价）；`hub handle` + `agent_run`；
  `--adopt` / `--submit`；**轨迹评测**（有规则 22/22 一致 / 无规则 22/22 选同一个工具）；
  **Critic**（第二个模型核对，只标记不拦）
- **阶段 3.4~3.9**：复盘指标（**按数据把 `refund_rate` 降级成展示项**）；
  **Case RAG**（检索/注入/引用校验）；**`hub brief`**（数字必须在输入里找到）；
  **通知 outbox**；**知识条目**；**报表导出**（PII 复查）
- **阶段 4**：认证（JWT 3 小时 + **服务端登出**：Redis 黑名单）；
  审计（**8 类动作在业务层写**）；CORS；**21 个读接口**（含 13 个图表）

### 修复
- `ToolSpec.idempotent` **声明了从没被网关读过** → 只读查询被永久缓存（读到旧数据）
  → 补**反例测试** ⑨b
- `tool_execution.proposal_id` NOT NULL → MCP 等外部调用没有提案 → 迁移改可空
- 「唯一字段用了不唯一的值」：`eval run_id` 只到分钟 → 两次运行合并成 38/38
- `analysis_run.trace_id` 用簇 id → 同簇第二次采纳撞唯一约束

### 新增规范（`工程规范.md`）
- **§六点六** 开关/字段类代码必须测**反例**
- **§六点七** 新接入一个调用方 = 一次隐含假设体检
- **§六点八** 改代码前先 grep 真实符号名（含 `tests/`）

---

## 说明：**没有 0.0.x**

阶段 0 之前是这个项目从零搭建 M1~M11 的过程，那时没有 CHANGELOG。
从这里往前看：`git log --oneline` 是最早的起点 ✅
