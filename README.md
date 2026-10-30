# 跨平台电商 AI 运营决策中枢

把淘宝、京东后台导出的 Excel，变成**有证据、有优先级、能执行**的运营决策。

```text
商家导出 xlsx  →  降噪 → 图片打标 → 差评归因 → 同款对齐 → 问题聚类 → 价格背离 → 优先级
                                                                              ↓
                                                               "先修这个：扎人面料 61 条"
```

**这不是一个"调 API 写总结"的项目**，重点在三件事：

1. **反幻觉**：每条结论都有原文子串证据，编造的直接丢弃；
2. **可执行**：输出是"改哪个商品、做什么动作、预期效果"，不是一段文字；
3. **可复现**：`python scripts/run_all.py` 一条命令从原始文件跑到优先级。

---

## 当前进度：9 / 11 个模块

| 模块 | 说明 | 状态 |
| --- | --- | --- |
| M1 | DQE 数据降噪（规则 9 条 + ML 水军分类） | ✅ |
| M2 | 多模态沙箱（图片阅后即焚 + 元数据剥离 + 本地 Vision） | ✅ |
| M3 | 归因链（多标签分类 + 按 topic×平台 归因 + 证据校验） | ✅ |
| M4 | 复合分析（聚类 / 价格背离 / 优先级） | ✅ |
| M5 | 工具网关（四道防线 + 风险分级 + 幂等 + 频控） | ✅ |
| M6 | 执行链（提案 → 审批 → 执行 → 回查 → 补偿/死信） | ✅ |
| **M7** | **调度与队列（Redis Stream + 断点续跑 + 崩溃恢复）** | ✅ |
| M8 | 可观测（trace/status/health）+ 评估门禁 | ✅ |
| M11 | 业务数据面（同款对齐 `sku_mapping`） | ✅ |
| M9 / M10 | 效果复盘 / 运营交互 | ⬜ 待做 |
| — | 部署、前端 | ⬜ 待做 |

**实测数据**（mock 数据，3981 条评价）：

```text
降噪      3981 → keep 2318（降噪率 41.8%，差评零误杀）
图片      100 条带图评价 / 172 张图 / pHash 去重后调 Vision 50 次 / 沙箱残留 0
归因      450 条差评分类（准确率 95.8% vs 人工标注）/ 12 个格子归因 / 证据逐字校验
对齐      20 对跨平台同款 / 30 个 canonical / 0 个错配
聚类      13 个问题簇（"面料扎人 55"、"掉色易旧 62"…），每个跨平台
价格      4 个"贵且差"异常（京东贵 54% 且差评率前 5%）
优先级    P0 1 个 / P1 3 / P2 5 / P3 4
提案      4 个（3 条回复自动执行 + 1 个改描述转人工待批）
全链      13 步 ~7.5 分钟（M2 图片 ~3 分钟 + M3 归因 ~3.6 分钟）
```

**M5/M6 的验收**（三个检查点全过）：

```text
A 工具（6 项）    改状态 / 回查 / 补偿
B 网关（16 项）   四道防线 + 风险分级，L2/L3 不执行
C 执行链（21 项） 重试 / 补偿回滚 / 死信 / 审批流 / 角色检查
```

**M8 的实测**（一次全链）：

```text
耗时  447s（M2+M3 占 88%）｜覆盖 98.4%（剩下 7s 是导入/训练/对齐）
成本  117,782 input tokens / 8,252 output ｜视觉占 68%
      一张图 = 1,597 tokens + 3.5 秒 ｜pHash 去重省了约 425 秒
门禁  20 条扰动用例 100%（硬门禁 19/19，软门禁 1/1），退出码 0
```

**M7 的实测**（崩溃恢复）：

```text
① Ctrl+C              当前任务未 ACK
② hub queue           RUNNING｜断点 dqe｜PEL 1
③ worker --idle 5     🔧 抢到闲置任务 → 从 dqe 之后开始 → DONE
   证明：任务没丢 / 断点续跑（不从头）/ XAUTOCLAIM 真的会抢
验收  21 项全过（断点/防重/重试/死信/重放/对账/PEL）
```

| 模型 | 用途 | 大小 |
| --- | --- | --- |
| `qwen2.5:7b` | 文本：分类 / 归因 / 簇名 / 决策 | 4.7GB |
| `qwen2.5vl:3b` | 视觉：图片打标（VLM 才能读图） | 3.2GB |

---

## 快速开始

```bash
# 0. 环境：Python 3.11 + PostgreSQL + Ollama
conda create -n e-commerce_operations_hub python=3.11
pip install -r backend/requirements.txt

# 模型：文本和视觉分开配（VLM 为了看图牺牲了语言能力）
ollama pull qwen2.5:3b        # 文本：M3 分类/归因、M4 簇名、M5/M6 决策
ollama pull qwen2.5vl:3b      # 视觉：M2 图片打标
# 只拉 qwen2.5vl:3b 也能跑（文本会走 VLM 模式，效果略差）

# .env（复制 .env.example 后填）
#   HUB_STORAGE__URL=postgresql+psycopg://postgres:密码@localhost:5432/E_commerce_Operations
#   HUB_LLM__MODEL=qwen2.5:3b        # 文本模型
#   HUB_VISION__MODEL=qwen2.5vl:3b   # 视觉模型

# 1. 建库 + 迁移
cd backend
alembic upgrade head

# 2. 生成 mock 数据（首次）
python scripts/generate_mock.py

# 3. 一条命令跑完整条链（约 6~7 分钟）
python scripts/run_all.py
```

**单独跑某一步**（不用重跑全链）：

```bash
python -m hub.cli dqe          # M1 降噪
python -m hub.cli mm           # M2 图片
python -m hub.cli topic        # M3 归因
python -m hub.cli align        # M11 同款对齐
python -m hub.cli cluster      # M4 聚类
python -m hub.cli price        # M4 价格背离
python -m hub.cli priority     # M4 优先级
python -m hub.cli propose      # M5 生成提案 + 过网关
python -m hub.cli pending      # 看待审批
python -m hub.cli approve <id> --by manager --note "同意"
python -m hub.cli reject  <id> --by operator --note "不需要"

# M8 可观测 / 评估
python -m hub.cli trace                # 最近 10 次运行
python -m hub.cli trace <trace_id>     # 单次链路树（哪一步慢）
python -m hub.cli status               # 业务数据现状（含 GMV 影响面）
python -m hub.cli health               # 最近一次运行健康度（耗时/token/降级）
python -m hub.cli eval                 # 20 条扰动用例（有退出码，可接 CI）

# M7 调度（需要 Redis）
python -m hub.cli enqueue              # 把一条全链塞进队列
python -m hub.cli worker               # 常驻消费（Ctrl+C 退出）
python -m hub.cli worker --once        # 只处理一条（测试用）
python -m hub.cli queue                # 队列深度 / PEL / 延迟 / 死信
python -m hub.cli queue --dlq          # 死信明细
python -m hub.cli dlq replay --id <前8位>   # 重放死信（保留断点）
```

### Redis（M7 前置）

```bash
docker run -d --name hub-redis --restart unless-stopped \
  -p 127.0.0.1:6379:6379 -v hub-redis-data:/data \
  redis:7.4-alpine redis-server --appendonly yes --requirepass hub_dev_2026 \
  --maxmemory 256mb --maxmemory-policy noeviction
```

`.env` 里配：

```ini
HUB_QUEUE__URL=redis://:hub_dev_2026@127.0.0.1:6379/0
```

**四个参数都不是可选的**：`127.0.0.1` 绑本机（暴露到局域网=挖矿目标）、`appendonly` 持久化（否则 Redis 重启丢任务）、`requirepass` 密码、`noeviction` 内存满时报错（LRU 会**静默删掉排队的任务**）。

---

## 目录结构

```text
backend/
  hub/
    contracts/      数据契约（Pydantic，唯一真相）
    persistence/    35 张表 + 各个 Repo
    ingestion/      适配器（淘宝/京东的字段映射、PII 脱敏、坏行隔离）
    quality/        M1 DQE（规则 + ML）
    multimodal/     M2 沙箱 + pHash + Vision
    analysis/       M3 归因 · M4 对齐/聚类/价格/优先级
    llm/            Ollama 客户端
    cli.py          所有命令入口
  scripts/          数据生成、全链编排
  tests/            check_*.py（每步一个验证脚本）
  alembic/          迁移
docs/
  开发思路.md                    ⭐ 从零到现在的完整过程（含 30+ 个踩坑）
  跨平台电商全息运营中枢_开发文档.md   设计文档（3313 行）
  工程规范.md                    踩坑换来的规则（数据冻结 / 排序 / upsert）
  项目功能展示.md                目标形态（六维度）
```

---

## 四个关键设计

**① 证据必须能被原文子串匹配**

```text
模型说："差评集中在袖口开线"
证据：  quote="袖口开线" ← 必须在原文里逐字存在
        找不到 → 丢弃这条证据
```

不是"语义相似"，是**精确子串**——语义相似会放过"拼接幻觉"。

**② LLM 只做理解和解释，不做数值计算**

数数、聚合、排序全用 SQL/Python：`450 条差评按 topic×平台 分组` 是 `GROUP BY`，不是让模型数。

**③ 小模型（3B）要用封闭格式**

实测：多标签任务用 JSON 数组 → 3B 陷入复读（0/10）；用行格式 `R1: quality,size | 3` → 8/10。
**小模型不是"能力差的模型"，是"必须按它的习惯喂的模型"。**

**④ 反幻觉四道防线**

| 防线 | 做法 |
| --- | --- |
| 证据子串校验 | 引用的原句必须逐字存在于源文本 |
| 执行后回查 | 动作执行后重新查询状态，不一致就回滚 |
| Case RAG | 建议必须引用历史案例（M5 之后） |
| INCONCLUSIVE 兜底 | 证据不足时输出"无法判断"，不硬编 |

---

## 已知局限（诚实说明）

- **mock 数据规整，指标虚高**：分类准确率 95.8%，真实电商评论预计 70~80%；
- **LLM 环节有抖动**：`temperature=0` 也不能保证完全确定，两次跑数字差 ±2~5%；
- **销量只覆盖 6/25 商品**：优先级的 `reach` 因子大多退回"评价量占比"；
- **`trend` / `actionability` 是常量**：分别依赖历史环比和 M5/M6 的工具信息；
- **只做了 2/6 个业务维度**：订单/发货/到账/投放未实现。

### 关于"AI 回复"：只做**起草**，不做**发送**

`review.draft_reply` 是 **L1**：只写草稿，**永远不会发给买家**。这是**故意的**，不是没做完：

| 为什么 | 说明 |
| --- | --- |
| 说错话的代价 | 模型可能承诺"给您补发"，但它没这个权限；买家记住了就是二次投诉 |
| 平台处罚 | 淘宝/京东对"敷衍回复"（同一句模板刷屏）、广告、诱导好评都有处罚规则 |
| 规模效应 | 人工一天回 50 条错 2 条能兜住；系统一天回 1000 条错 50 条兜不住 |

**真实电商里"全自动回复差评"这件事，做得好的商家也不做**（他们做"自动回复好评"，差评一定人工）。

**要真接进业务，缺的不是技术，是授权**：

```text
企业资质 + 开放平台应用审核（个人开发者拿不到评价回复权限）
+ 店铺主账号 OAuth（客服子账号通常只有查看权限）
+ 平台内容规则（不得广告/诱导好评/批量刷同一句）
+ 写接口配额（超限会临时封禁）
```

我们是"**商家自助**"定位（导出文件导入），所以前提是**商家自己授权**。

**架构上已经支持"发出去"**：加一个 **L3** 的 `review.publish_reply` 即可——L3 的规则和改价一模一样（网关拦截 → 建待办 → 人批准 → 执行 → 回查平台上真的有这条回复）。**起草自动、发送人点**，这才是真实业务里最被接受的形态。

详见 [`docs/开发思路.md`](docs/开发思路.md) 的「为什么只做起草，不做发送」。

---

## 文档索引

| 文档 | 作用 |
| --- | --- |
| [`docs/开发思路.md`](docs/开发思路.md) | **从零到现在**的完整过程：每步的目标、决策、踩坑、遗留 |
| [`docs/跨平台电商全息运营中枢_开发文档.md`](docs/跨平台电商全息运营中枢_开发文档.md) | 设计文档：架构、数据模型、接口、部署 |
| [`docs/工程规范.md`](docs/工程规范.md) | 踩坑换来的规则（数据冻结、排序、upsert、LLM 格式测试） |
| [`docs/项目功能展示.md`](docs/项目功能展示.md) | 目标形态（六维度业务视角） |

---

## 技术栈

Python 3.11 · PostgreSQL · SQLAlchemy 2.0 · Alembic · Pydantic v2 · scikit-learn · jieba · Ollama（qwen2.5vl:3b 本地跑，数据不出内网）
