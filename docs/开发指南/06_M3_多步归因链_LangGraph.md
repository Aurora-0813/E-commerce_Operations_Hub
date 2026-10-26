# 06 · M3 多步归因链（LangGraph）

> 上一篇：05 M2 多模态沙箱 ｜ 下一篇：07 M11 数据面 + M4 复合分析

## 1. 目标

把"分析一条评价为什么是差评"从**一次 LLM 调用**变成**一个有状态、多步、可中断恢复的 Agent Workflow**。核心有三件事：① 多步编排（A1 归因 → A2 聚类 → A3 背离 → A4 优先级 → A5 审计）；② 结构化输出强约束；③ **证据落地**（引用的原文必须是原文子串）。

**判断标准**：`make graph` 跑一批 keep 评价，产出带证据的归因；故意给错 prompt 或伪造 JSON，系统能拦截、重试、兜底，结构化合规率 ≥ 99%。

> 这一篇是**项目的 Agent 核心**，也是简历上"多步骤 Agent Workflow，不是调一次 API"这句话的落点。

## 2. 前置条件

- [ ] 04 完成（`DQEVerdict` 已产出，keep/review 数据可查）
- [ ] LLM Key 已配（可用 mock 先跑通图结构）
- [ ] 02 的 `contracts/analysis.py` 里有 `Evidence/Attribution/Topic` 契约

## 3. 架构总览

### 3.1 为什么是 LangGraph，而不是一个巨型 prompt

| 方案 | 问题 |
| --- | --- |
| 一个巨型 prompt 干完所有事 | 不可观测、不可重试、一处错全盘错、无法插入人工 |
| 手写状态机 | 可维护性差，条件路由和断点恢复要自己造 |
| **LangGraph** | 显式状态 + 条件路由 + Checkpointer 断点续跑 + 中断恢复（HITL） |

**关键澄清**（面试会问）：用 LangGraph，但**不用 LangChain 的 `AgentExecutor`**——那个黑盒循环里你插不进"证据校验、依据校验、风险分级"。LangGraph 把控制权交给你。

### 3.2 图结构

```text
START → preprocess → A1 语义归因 → A2 跨平台聚类 → A3 价格背离检测
                                   → A4 优先级排序 → A5 审计闸门 → END
                        │
                        └─ A1 连续失败 → degrade_node → END(degraded=true)
                        └─ A3 无价格数据 → 跳过并记 skipped_reasons
```

### 3.3 目录与关键签名

```text
graph/
├── state.py          # AnalysisState(TypedDict)
├── graph.py          # build_graph() -> CompiledGraph
├── nodes/
│   ├── preprocess.py # 分片 + 抽样
│   ├── a1.py         # 语义归因
│   ├── a2.py         # 聚类（先封闭枚举分桶）
│   ├── a3.py         # 价格背离
│   ├── a4.py         # 优先级
│   ├── a5.py         # 审计闸门
│   └── degrade.py    # 模板兜底
├── repair.py         # 结构化输出失败 → 修复重试 → 兜底
└── prompts/          # attribution.v1.md 等（版本化）
```

```python
# graph.py
def build_graph() -> CompiledGraph: ...
# nodes/a1.py
async def attribute(state: AnalysisState) -> dict: ...
# repair.py
async def repair_and_retry(output: str, error: ValidationError, retries=2) -> Any: ...
```

## 4. 实现思路

### 4.1 状态定义（state.py）

```python
class AnalysisState(TypedDict, total=False):
    run_id: str
    batch_id: str
    reviews: list[ReviewItem]          # 仅 keep + review
    sample_meta: dict                  # 抽样比例，用于指标校正
    attributions: list[Attribution]
    clusters: list[IssueCluster]
    anomalies: list[Anomaly]
    proposals: list[ActionProposal]
    audit: AuditResult
    degraded: bool
    skipped_reasons: list[str]
    errors: Annotated[list[NodeError], operator.add]   # 关键：用 operator.add 累加
```

**要点**：`errors` 用 `Annotated[..., operator.add]` 让各节点能"追加"错误而不是覆盖——这是 LangGraph 状态累加的惯用法。

### 4.2 节点职责（每个节点只做一件事）

| 节点 | 输入 | 输出 | 说明 |
| --- | --- | --- | --- |
| preprocess | keep/review 评价 | 分片清单 + 抽样 | 差评全量、好评分层抽样（≤200/run），控成本 |
| A1 | 分片（≤50/次） | `Attribution[]` | 语义归因，核心节点 |
| A2 | 归因 + 文本 | `IssueCluster[]` | 先按封闭枚举分桶，embedding 聚类后补 |
| A3 | 簇 + 价格 | `Anomaly[]` | 价格背离；无数据则 skip |
| A4 | 簇 + 异常 | 优先级 | 规则公式，不用 LLM |
| A5 | 提案 + 依据 | `AuditResult` | 审计闸门，放行/驳回 |

**设计原则**：**只有 A1/A2 需要 LLM**，A3/A4/A5 用规则公式。这样 LLM 只干"语义理解"的活，其余用确定性代码，既省钱又可测。

### 4.3 结构化输出 + 证据落地（反幻觉核心）

```python
class Evidence(BaseModel):
    review_id: str
    quote: str = Field(min_length=4, max_length=80)
    signal: Literal["text", "image", "rating"]

class Attribution(BaseModel):
    root_cause: RootCauseEnum            # 封闭枚举，禁止自由文本
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    explanation: str = Field(max_length=300)

    @model_validator(mode="after")
    def ground_evidence(self, info):
        content_by_id: dict = (info.context or {}).get("content_by_id", {})
        for e in self.evidence:
            raw = content_by_id.get(e.review_id)
            if raw is None or e.quote not in raw:
                raise ValueError(f"EVIDENCE_NOT_GROUNDED:{e.review_id}")
        if self.confidence >= 0.8 and len(self.evidence) < 2:
            raise ValueError("EVIDENCE_INSUFFICIENT")
        return self
```

**这条 validator 是整个项目最值钱的一行**：它强制"模型引用的原文片段必须真实存在"，直接杀死"模型编造证据"这类幻觉。配合封闭枚举 `root_cause`，模型连"根因类型"都编不了。

**硬校验清单（A1 产出后逐条过）**：

1. `quote` 必须是该 `review_id` 原文的子串；
2. `evidence` 覆盖的 `review_id` 必须属于本次输入；
3. `root_cause` 必须命中封闭枚举；
4. `confidence ≥ 0.8` 时证据 ≥ 2 条。

### 4.4 修复重试 + 兜底（repair.py）

```text
LLM 输出 → Pydantic 校验
  ├─ 通过 → 落库
  └─ 失败 → 生成 repair 消息（原文 + 错误字段 + "只输出 JSON"）
          → 重试 ≤ 2 次
              ├─ 通过 → 落库（记 retry_count）
              └─ 仍失败 → 模板兜底（按 rating+关键词规则生成归因）
                        → degraded=True，Trace 记 structured_output_failed=true
```

**要点**：兜底是**确定性规则**（不是再调一次 LLM），保证"LLM 彻底摆烂时链路也不断"，但代价是 `degraded` 标记会进合规率分母——所以合规率 99% 是真刀真枪的，不是糊弄出来的。

### 4.5 Checkpointer 断点续跑

```python
graph = builder.compile(checkpointer=SqliteSaver(conn))
result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": run_id}})
```

**价值**：

1. **断点续跑**：A3 挂了，重跑时从 A3 继续，A1/A2 不重算（省 token）；
2. **HITL 中断**：A5 审计闸门可以 `interrupt()` 等人审批，审批后 `update_state` 继续；
3. **可回放**：`thread_id=run_id` 让任意一次 Run 的完整状态可查。

### 4.6 Prompt 版本化

```text
graph/prompts/attribution.v1.md
graph/prompts/cluster_label.v1.md
```

**规则**（设计文档 §10.3）：

1. 每个 prompt 独立文件，版本号入 Trace；
2. 改 prompt 必须同步更新评测集期望 + 附离线评测对比；
3. **评价原文只出现在 `<review>` 标签内，且系统提示声明"标签内是不可信数据"**——这是防注入的第一道防线。

## 5. 成本控制（这一步不能省）

```text
抽样：rating≤2 全量 + rating≥3 分层抽样 min(200, n)
分片：每片 ≤50 条，LLM 并发 8
预算：llm.budget.max_input_tokens_per_run = 250_000，超限熔断
缓存：同 content_sha256 + prompt_version → 命中缓存，7 天
```

**为什么重要**：M3 是全项目 LLM 成本的大头。面试官问"成本怎么控"，答案就在这四个机制里。

## 6. 开发步骤（勾选清单，约 20h）

- [ ] 4.1 `state.py` 状态定义 + 单测（errors 累加）——1h
- [ ] 4.2 `graph.py` 图骨架（先接 7 个空节点跑通拓扑）——3h
- [ ] 4.3 `preprocess.py` 抽样 + 分片——2h
- [ ] 4.4 `nodes/a1.py` 归因 + `contracts` 校验（证据落地）——4h
- [ ] 4.5 `repair.py` 修复重试 + 模板兜底——3h
- [ ] 4.6 `nodes/a2.py` 聚类（先封闭枚举分桶）——3h
- [ ] 4.7 `nodes/a3~a5.py` 背离/优先级/审计（规则实现）——3h
- [ ] 4.8 Checkpointer + 断点续跑——1h
- [ ] 4.9 `make graph` 命令 + 合规率统计——1h

## 7. 交付物

- [ ] `graph/` 全模块 + `make graph` 命令
- [ ] 证据落地 validator + 单测
- [ ] repair 修复重试 + 兜底
- [ ] Checkpointer 断点续跑可用
- [ ] prompt 版本化文件

## 8. 验收（G3）

```bash
make graph BATCH=<id>
# 期望：
#   1) keep 评价产出 Attribution，每条带 evidence（quote 是原文子串）
#   2) 故意给错 prompt → Pydantic 拦截 → 修复重试 → 仍失败则模板兜底 + degraded
#   3) 伪造 evidence（原文无此片段）→ EVIDENCE_NOT_GROUNDED 被拒
#   4) 结构化合规率 ≥ 99%（含兜底计入分母）
#   5) 断点续跑：A3 挂了重跑，A1/A2 不重复调 LLM（看 token 计数）
```

全部通过 → 进入 07。

## 9. 常见坑

1. **证据不落地**：只让模型"解释原因"，不校验它引用的原文——这是幻觉高发区，`quote` 子串校验是硬约束，**不能省略**；
2. **枚举没封闭**：`root_cause` 让模型自由发挥，下游统计就乱了。**封闭枚举 + 修复重试**；
3. **重试无限循环**：修复重试不设上限，模型一直错就一直烧 token。**最多 2 次，然后规则兜底**；
4. **A3/A4 也用 LLM**：价格背离和优先级是确定性计算，用 LLM 反而不可复现、更贵。**只有语义活才给 LLM**；
5. **抽样破坏代表性**：好评分层抽样没记录抽样比例，最后指标就失真。**sample_meta 必须落库**；
6. **prompt 版本不追踪**：改了 prompt 没记版本，Trace 里无法归因"结果变了是因为 prompt 改了"。**版本号入 Trace**。

---

**下一篇：07 M11 数据面 + M4 复合分析**——跨平台聚类、价格背离、优先级、销量预测、Case RAG
