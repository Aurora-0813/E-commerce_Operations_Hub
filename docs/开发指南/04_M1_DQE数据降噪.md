# 04 · M1 DQE 数据降噪引擎

> 上一篇：03 数据接入与 PII ｜ 下一篇：05 M2 多模态沙箱

## 1. 目标

在数据进入 LLM 之前，用"规则轨 + ML 轨"双轨把无效数据（水军、灌水、刷单、可疑好评）拦下来，输出 `keep / review / drop` 三种判定，并且**可插拔降级**——ML 模型挂了自动退回规则，主链永不中断。

**这是你简历的第一个真实数字**：降噪率（`drop+review` 占比）。做完这篇，你能说"我的系统前置拦截了 62% 的无效评价"——而且这个数字是真的跑出来的。

**判断标准**：`make dqe` 跑一批真实数据，出降噪率；删掉模型文件再跑，自动切规则轨，结果仍然正确。

## 2. 前置条件

- [ ] 03 完成（`Record` 已入库，`make p0` 可用）
- [ ] 有少量标注样本（哪怕 200 条）用于校准阈值

## 3. 架构总览

### 3.1 数据流

```text
Record（清洗后）
  → 归一化（文本规整）
  → 规则轨（9 条规则）──┐
  → ML 轨（水军分类 + 评分异常）──┤→ 决策融合 → DQEVerdict{keep|review|drop}
  → 落库（verdict + score + hit_rules + engine）
```

### 3.2 目录与关键签名

```text
dqe/
├── normalize.py      # 文本归一化
├── rules.py          # RuleEngine，9 条规则
├── ml.py             # SpamClassifier / AnomalyDetector
├── decide.py         # 决策融合（差评不静默丢弃）
├── engine.py         # 可插拔引擎 select_engine + 降级
├── pipeline.py       # run_dqe 批量入口
└── train.py          # 训练水军分类器/异常检测器（离线）
```

```python
# decide.py
def decide(hits: list[RuleHit], spam: float, anom: float,
           rating: int, cfg: DqeConfig) -> DQEVerdict: ...

# engine.py
class DqeEngine:
    def run(self, records: list[Record]) -> list[DQEVerdict]: ...
    @property
    def engine_name(self) -> str: ...   # "ml+rule" | "rule_only"
```

## 4. 实现思路

### 4.1 归一化（normalize.py）

把"看起来不同、其实是同一类"的文本规整，让规则和模型都更稳：

| 步骤 | 例子 | 目的 |
| --- | --- | --- |
| NFKC 全角转半角 | `ＧＢＫ` → `GBK` | 统一字符形态 |
| 去零宽字符 | 去掉 U+200B/U+FEFF | 防"隐形灌水" |
| 去 HTML 标签 | `<br/>` 等 | 清理导出残留 |
| 压缩重复字符 | `太太太太差` → `太差` | 防刷屏绕规则 |
| 统一空白 | 多个空格/换行 → 单空格 | 稳定长度判断 |

**注意**：归一化只做"规整"，**不做语义改变**——不要在这里做分词、不要删词。删除是决策层（规则/模型）的事。

### 4.2 规则轨（rules.py，9 条规则）

规则是**兜底**，也是**可解释性**的来源——每条命中都记录 `rule_id`，事后能说清"这条为什么被拦"。

| ID | 判定 | 动作 | 说明 |
| --- | --- | --- | --- |
| R001 | 文本长度 < 5 且无图片 | drop | 无意义短评 |
| R002 | 中文字符占比为 0 且无图片 | drop | 纯符号/外文灌水 |
| R003 | 标点/表情占比 > 80% | drop | 刷屏 |
| R004 | 命中联系方式正则（手机/微信/QQ/URL） | drop | 广告/导流 |
| R005 | 命中广告词库（加微/刷单/代发…） | drop | 广告 |
| R006 | 灌水模板（"此用户未填写评价"/"默认好评"） | drop | 系统默认 |
| R007 | 同批 simhash 近似重复（汉明 ≤3） | 保留首条，其余 drop | 批量复制 |
| R008 | 命中 Prompt 注入（"忽略以上"/"ignore previous"/"system:"） | drop 且计安全事件 | 攻击面 |
| R009 | `rating ≤ 2` | **不参与静默丢弃** | 见 4.4 差评保护 |

**规则引擎实现**：每条规则一个独立函数 `rule_r001(record) -> RuleHit | None`，用装饰器注册进 `RuleEngine`。好处：加规则不改主流程，单测每条规则。

### 4.3 ML 轨（ml.py）

**两个轻量模型，都不依赖 GPU**：

#### (1) 水军分类器 `SpamClassifier`

```text
特征：TfidfVectorizer(char_wb 2-4) + TfidfVectorizer(jieba 词 1-2) 拼接
模型：LogisticRegression(class_weight='balanced')
输出：spam_score ∈ [0,1]
```

#### (2) 评分异常检测 `AnomalyDetector`

```text
特征（8 维，结构化，不依赖文本）：
  rating, char_len, has_image, 下单-评价时延,
  用户历史评价数, 同文本近1h出现次数, emoji计数, 情感极性
模型：IsolationForest(contamination=0.05)
输出：anomaly_score（归一化到 [0,1]）
```

**训练与产物管理（train.py）**：

```text
产物：models/dqe/spam_lr_{version}.joblib + model.json
model.json 内容：版本、训练集指纹(sha)、P/R/F1、特征维度、创建时间
加载：engine 启动时校验 joblib 存在 + model.json 哈希匹配 → 才启用 ML 轨
```

> 为什么要有 `model.json`：防止"换了个训练集但忘了重新部署"，版本对不上就自动降级，避免静默跑错模型。

### 4.4 决策融合（decide.py，核心逻辑）

```python
def decide(hits, spam, anom, rating, cfg):
    # 1) 规则硬 drop
    if any(h.is_hard_drop for h in hits):
        # 差评保护：rating<=2 时硬规则降级为 review（R008 注入除外）
        downgrade = rating <= 2 and not any(h.rule_id == "R008" for h in hits)
        return verdict("review" if downgrade else "drop",
                       reason=hits[0].rule_id, engine=cfg.engine)

    # 2) ML 水军
    threshold = cfg.spam_threshold_negative if rating <= 2 else cfg.spam_threshold
    if spam >= threshold:
        return verdict("review" if rating <= 2 else "drop",
                       reason="ML_SPAM", engine=cfg.engine)

    # 3) 评分异常（可疑好评）
    if anom >= cfg.anomaly_threshold and rating >= 4:
        return verdict("review", reason="SUSPECT_FAKE_POSITIVE", engine=cfg.engine)

    return verdict("keep", engine=cfg.engine)
```

**三种决策语义（贯穿全系统，别混）**：

| verdict | 含义 | 进分析？ | 计入降噪率？ |
| --- | --- | --- | --- |
| `keep` | 正常 | 是 | 否 |
| `review` | 可疑但有价值，打标待复核 | 是 | 是 |
| `drop` | 噪音，不分析（仍落库可回溯） | 否 | 是 |

**核心设计决策：差评永不静默丢弃。** `rating ≤ 2` 时，规则/ML 给出的 `drop` 一律降级为 `review`。理由：**误杀一条真实差评的代价，远高于多花一点 Token 处理它。** 唯一例外是 R008（Prompt 注入）——为阻断攻击面仍判 drop。

### 4.5 可插拔引擎（engine.py，降级开关）

```yaml
dqe:
  engine: hybrid        # hybrid | ml | rule
  fallback_on_error: true
```

```python
def select_engine(cfg) -> Engine:
    if cfg.engine == "rule":
        return RuleOnlyEngine()            # 永远可用
    try:
        spam = load_model("spam"); anom = load_model("anom")   # 校验哈希
        return HybridEngine(spam, anom)
    except ModelLoadError:
        log("dqe_degraded", engine="rule_only", reason=e)      # 打点 + 告警
        return RuleOnlyEngine()
```

**四条保障（对应设计文档 §2.8 铁律）**：

1. 模型文件缺失/哈希不匹配 → 捕获 → 切 `rule_only` → 打点 `dqe_degraded_total` → 告警，**主链继续**；
2. 每次输出的 `verdict.engine` 必须标注 `"ml+rule"` 还是 `"rule_only"`（可追溯）；
3. 降级发生时，报告里明确写"本次为规则模式"；
4. 单测覆盖：删掉模型文件 → `run_dqe` 不抛异常，结果 engine=rule_only。

### 4.6 姊妹能力：无效订单识别（复用同一套方法论）

DQE 的"规则轨 + 无监督轨 + 兜底"可以直接迁到订单上（对齐岗位 JD 的"无效订单识别"）：

| 轨 | 内容 |
| --- | --- |
| 规则轨 | 同收货地址/同 IP 多单、下单间隔过短、退款率异常、客单价离群 |
| 无监督轨 | 孤立森林（特征：订单间隔、金额、地址聚合度、退款行为） |
| 兜底 | 无标签时用规则 + 人工抽检（每批 100 单），小标注集滚动调阈值 |

**实现**：`dqe/order.py` 复用 `rules.py` 的规则引擎骨架 + `ml.py` 的孤立森林，只换特征和规则。**输出只报 `precision@抽检`，不宣称"准确率 95%"**——没标签就诚实说没标签。

## 5. 关键契约

```python
class DQEVerdict(BaseModel):
    review_id: str
    decision: Literal["keep", "review", "drop"]
    reason_code: str                 # R001..R009 / ML_SPAM / SUSPECT_FAKE_POSITIVE
    spam_score: float
    anomaly_score: float
    hit_rules: list[str]             # 命中的规则 ID，可解释
    engine: Literal["ml+rule", "rule_only"]
    model_version: str | None
```

## 6. 开发步骤（勾选清单，约 15h）

- [ ] 4.1 `normalize.py` 五种归一化 + 单测——2h
- [ ] 4.2 `rules.py` 9 条规则 + 每条单测——4h
- [ ] 4.3 `decide.py` 决策融合 + 差评保护单测——2h
- [ ] 4.4 `ml.py` 两个模型（先可返回占位 0，规则轨先行）——3h 【P1】
- [ ] 4.5 `train.py` 训练脚本 + `model.json` 产物管理——3h 【P1】
- [ ] 4.6 `engine.py` 可插拔 + 降级单测——1h
- [ ] 4.7 `order.py` 无效订单识别——2h 【P1】
- [ ] 4.8 `make dqe` 命令 + 降噪率统计——1h

> **策略**：先做 4.1–4.3 + 4.6（规则轨），**跑出第一个降噪率**；ML 轨（4.4/4.5）有标注数据后再补。这样第一周就能拿到简历数字。

## 7. 交付物

- [ ] `dqe/` 全模块 + `models/dqe/` 产物目录
- [ ] `make dqe` 命令 + 降噪率报告
- [ ] 降级单测（删模型不崩）
- [ ] （P1）`train.py` + 无效订单识别

## 8. 验收（G1）

```bash
make dqe BATCH=<id>
# 期望：
#   1) 输出 keep/review/drop 三种判定，各有计数
#   2) 降噪率（(review+drop)/total）有具体数值
#   3) 每条 drop 都带 hit_rules 或 ML_SPAM（可解释）
#   4) rating<=2 的评价：决定是 review 或 keep，绝不 drop（除非 R008）
#   5) 删掉 models/dqe/ 下模型文件 → 重跑不报错，engine=rule_only
#   6) 降噪率 ≥ 60%（用黄金集校准阈值）
```

全部通过 → 进入 05。

## 9. 常见坑

1. **差评被误杀**：规则里"长度<5 就 drop"会把"太差"这类真实差评拦掉。**R009 保护是硬约束**，漏了它等于把投诉当垃圾；
2. **阈值拍脑袋**：`spam_threshold=0.75` 是拍出来的，必须用标注集调——**阈值写进配置，不写死**；
3. **模型训练集和线上分布不一致**：用 2025 年数据训、2026 年数据跑，效果必崩。`model.json` 记训练集指纹，就是为了发现这个；
4. **降级静默**：模型加载失败却不打点不告警，等于"无声地降低了能力"。**降级必须打点 + 告警 + 标注 engine**；
5. **review 和 drop 混为一谈**：`review` 还进分析、`drop` 不进。下游（M3）按 `decision` 分流，定义不清会漏数据；
6. **规则写成一个大 if-else**：9 条规则堆在一个函数里，改一条动全身。**每条规则独立函数 + 装饰器注册**。

---

**下一篇：05 M2 多模态沙箱**——图片阅后即焚，tmpfs + pHash + 覆写销毁 + 审计
