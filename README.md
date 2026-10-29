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

## 当前进度：5 / 11 个模块

| 模块 | 说明 | 状态 |
| --- | --- | --- |
| M1 | DQE 数据降噪（规则 9 条 + ML 水军分类） | ✅ |
| M2 | 多模态沙箱（图片阅后即焚 + 元数据剥离 + 本地 Vision） | ✅ |
| M3 | 归因链（多标签分类 + 按 topic×平台 归因 + 证据校验） | ✅ |
| M4 | 复合分析（聚类 / 价格背离 / 优先级） | ✅ |
| M11 | 业务数据面（同款对齐 `sku_mapping`） | ✅ |
| M5~M10 | 决策网关 / 执行链 / 调度 / 可观测 / 复盘 / 运营交互 | ⬜ 待做 |
| — | 部署、前端 | ⬜ 待做 |

**实测数据**（mock 数据，3981 条评价）：

```text
降噪      3981 → keep 2318（降噪率 41.8%，差评零误杀）
图片      100 条带图评价 / 172 张图 / pHash 去重后调 Vision 50 次 / 沙箱残留 0
归因      450 条差评分类（准确率 94.5% vs 人工标注）/ 12 个格子归因 / 证据 100% grounded
对齐      20 对跨平台同款 / 30 个 canonical / 0 个错配
聚类      15 个问题簇（"扎人面料 61"、"洗后变形 62"…），每个跨平台
价格      4 个"贵且差"异常（京东贵 54% 且差评率前 5%）
优先级    P0 2 个 / P1 2 / P2 6 / P3 5
全链      12 步 ~395 秒
```

---

## 快速开始

```bash
# 0. 环境：Python 3.11 + PostgreSQL + Ollama（qwen2.5vl:3b）
conda create -n e-commerce_operations_hub python=3.11
pip install -r backend/requirements.txt
ollama pull qwen2.5vl:3b

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
```

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

- **mock 数据规整，指标虚高**：分类准确率 94.5%，真实电商评论预计 70~80%；
- **LLM 环节有抖动**：`temperature=0` 也不能保证完全确定，两次跑数字差 ±2~5%；
- **销量只覆盖 6/25 商品**：优先级的 `reach` 因子大多退回"评价量占比"；
- **`trend` / `actionability` 是常量**：分别依赖历史环比和 M5/M6 的工具信息；
- **只做了 2/6 个业务维度**：订单/发货/到账/投放未实现。

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
