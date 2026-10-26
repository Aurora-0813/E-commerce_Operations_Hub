# 03 · 数据接入与 PII（适配器）

> 上一篇：02 项目骨架与契约层 ｜ 下一篇：04 M1 DQE 数据降噪

## 1. 目标

把淘宝/京东后台导出的**真实文件**（CSV/XLSX，多 sheet、脏表头、GBK 编码、千分位金额、好中差评、合并单元格）解析成统一结构的 `RawBatch` / `Record`，做到：表头自动探测、编码自动识别、字段映射配置化、坏行隔离不阻塞、PII 直接丢弃。

**判断标准**：`make p0 FILE=data/raw/jd_review.xlsx` 一条命令，把任意一份导出文件变成"干净的结构化数据 + 一份导入报告（哪些列缺失、哪些行坏了、丢了多少 PII）"。

> 这一篇不写任何 LLM / 模型，纯数据工程。**它决定后面所有模块的地基质量**——垃圾进、垃圾出。

## 2. 前置条件

- [ ] 02 完成（`make skeleton` 能跑通、契约层就位）
- [ ] `data/raw/` 下至少 1 份真实淘宝 + 1 份真实京东导出文件
- [ ] 已打开文件看过一遍：列名、表头行数、编码、金额格式、日期格式

## 3. 架构总览

### 3.1 数据流

```text
文件(xlsx/csv)
  → 表头探测 → 编码探测 → 读成"行"
  → 字段映射（源列 → 目标字段）
  → 类型清洗（金额/时间/评分/多图）
  → 逐行校验 → 分流：
        ├─ 合法行 → Record（入库）
        ├─ 坏行   → bad_rows/{batch_id}.csv（隔离，不阻塞）
        └─ PII 列 → 直接丢弃，只计数（合规）
  → 输出 RawBatch + MappingReport
```

### 3.2 目录与关键签名

```text
ingress/
├── base.py           # Adapter 协议 + 公共管线
├── taobao.py         # 平台差异（评分映射、列别名）
├── jd.py             # 平台差异
├── mapping.py        # 加载/应用 config/mappings/{platform}.yaml
├── header.py         # 表头探测
├── encoding.py       # 编码探测
├── cleaner.py        # 类型清洗（金额/时间/评分/多图）
├── pii.py            # PII 识别与丢弃
└── report.py         # 生成 MappingReport
```

```python
# base.py —— 唯一的对外入口
class Adapter(Protocol):
    platform: str
    data_type: Literal["review", "product", "order"]
    def parse(self, path: Path) -> ParseResult:
        # 返回 RawBatch + 清洗后的 Record 列表 + MappingReport

# cleaner.py —— 每个清洗函数返回 (值, 是否合法, 原因)
def clean_price(raw: str) -> tuple[Decimal | None, bool, str]
def clean_ts(raw: str) -> tuple[datetime | None, bool, str]
def clean_rating(raw: str, fallback: str | None) -> tuple[int | None, bool, str]
```

## 4. 六个环节的实现思路

### 4.1 表头探测（header.py）

**问题**：导出文件第一行往往不是表头——可能有"数据说明"、空行、店铺名、多级表头（"评价内容/追加评价"）。

**思路**：

1. 读前 10 行，对每行统计"命中了多少个已知列名"（已知列名来自 `config/mappings/` 里所有 `source_column`）；
2. 命中数 ≥ 3（阈值可配）的那一行 = 表头行；
3. 记录 `header_row`，从它之后才按数据读；
4. 前 10 行都命中不了 → 抛 `CPP-1001 HEADER_NOT_FOUND`，**整批拒绝，不猜**。

```text
示例（京东评价导出）：
 第1行：京东商城 评价数据 导出时间：2026-10-01      ← 命中 0，跳过
 第2行：（空行）                                   ← 跳过
 第3行：评价编号 | 商品名称 | 评价内容 | 评价星级 | 评价时间   ← 命中 4，表头！
```

### 4.2 编码探测（encoding.py）

**问题**：淘宝导出常见 GBK，京东常见 UTF-8；还有带 BOM 的 UTF-8、以及个别"混编码"文件。

**思路**：

1. 先读原始字节，检测 UTF-8 BOM（`\xef\xbb\xbf`），有则直接按 UTF-8 去 BOM；
2. 尝试 UTF-8 解码，失败 → 尝试 GBK；
3. 解码成功后做**二次校验**：抽样 100 个单元格，中文字符占比 > 30% 判为"解码正确"（防"GBK 字节被错当 Latin-1"的静默乱码）；
4. 校验不过 → `CPP-1002 ENCODING_UNSUPPORTED`，并在报告里附上前 3 个可疑单元格。

> 关键教训：**不要信 `charset` 探测器给出的结果**（`chardet` 对 GBK/短文本误判率高），"先 UTF-8 后 GBK + 中文占比校验"这条规则比任何探测库都稳。

### 4.3 字段映射（mapping.py + config/mappings/）

**问题**：同一含义，两平台列名不同（淘宝"评价等级" vs 京东"评价星级"）；还有同义词（"评价内容"/"评价文本"）。

**思路**：映射表**配置化**，新增平台只加 YAML，不改代码。

```yaml
# config/mappings/jd.yaml
platform: jd
columns:
  platform_review_id: {source: [评价编号, 评价ID], required: true}
  rating:             {source: [评价星级, 星级],    required: true, kind: stars}
  content:            {source: [评价内容, 评价文本], required: true}
  review_time:        {source: [评价时间, 时间],    required: true}
  image_urls:         {source: [晒图, 图片地址],    required: false}
  spu_price:          {source: [商品价格, 单价],    required: false, transform: price}
pii_columns: [买家昵称, 收件人, 手机号, 地址, 订单号]
```

**要点**：

1. `source` 是**候选列表**，按顺序匹配第一个出现的列（兼容同义词）；
2. `required: true` 的列整批缺失 → 拒绝导入并报列名（不静默跳过）；
3. `pii_columns` 独立声明，映射阶段就标记为"只计数、不落地"。

### 4.4 类型清洗（cleaner.py）

**四个最难的类型**，逐个给规则：

| 字段 | 脏数据形态 | 清洗规则 |
| --- | --- | --- |
| 金额 | `￥1,299.00` / `1299 元` / `1.3k` | 去货币符号、去千分位逗号、去空格 → `Decimal`，**禁止 float**（精度） |
| 时间 | `2026/10/1` / `2026-10-01 12:00:00` / Excel 序列号 `45630` | 字符串优先按 `Asia/Shanghai` 解析；纯数字判为 Excel 日期序列（`datetime(1899,12,30)+n天`） |
| 评分 | `好评` / `中评` / `差评` / `★★★★☆` / 数字 `4.5` | 中文→5/3/1；星星→数实心星；数字→直接取。**有星级优先星级**，两者冲突记 `RATING_CONFLICT` |
| 多图 | `url1,url2` / `url1|url2` / 空 | 分隔符自适应（`,;|空格`），去重，最多取 9 张，保留原始顺序（首图用于主分析） |

**清洗函数统一约定**：返回 `(值, ok, reason)` 三元组——失败不抛异常，把原因记下来走坏行隔离，**不让一条脏数据拖垮整批**。

### 4.5 坏行隔离（report.py）

**问题**：5 万行里有 300 行脏的，不能因为这 300 行整批失败，也不能静默丢掉。

**思路**：

1. 逐行清洗，`ok=False` 的行写进 `out/bad_rows/{batch_id}.csv`，**保留原始行 + 列名 + 失败原因 + 行号**；
2. 合法行继续走；`MappingReport` 里记 `row_ok / row_bad / bad_row_breakdown`；
3. 单行必填缺失 → 隔离；**整列**必填缺失 → 拒绝（见 4.3）。

```text
bad_rows 文件格式（人能看懂、能被 re-import）：
行号, 原始数据, 失败列, 失败原因
1234, "...", 评价星级, "无法解析: 好评"
```

### 4.6 PII 过滤（pii.py，合规硬任务）

**要求**（设计文档红线）：文本导入层强制 PII 过滤，不提供原始数据导出。

**思路**：

1. `pii_columns` 里的列（买家昵称/收件人/手机号/地址/订单收件信息）在映射阶段就**标记丢弃**；
2. 额外做**内容级正则扫描**：正文里若混入手机号/身份证/微信号/QQ/银行卡号，把命中片段**打码为 `[PII]`**（不能整条丢弃，因为要保留评价内容）；
3. 丢弃/打码**只计数**，数量写进 `MappingReport.pii_stats`，**不落库、不写日志**（日志里连"命中了一个手机号"都不留原文）；
4. 单测强制断言：解析结果里不存在任何 `pii_columns` 的原始值。

> 这是合规红线，不是可选项。做的时候就想清楚："别人拿到数据库，能不能还原出买家手机号？"——不能，才算过。

## 5. 关键契约（已在 02 定义，这里确认字段）

```python
class BadRow(BaseModel):
    line_no: int
    raw: str
    column: str | None
    reason: str

class RawSheet(BaseModel):
    name: str
    header_row: int
    columns: dict[str, str]      # 归一列名 -> 原始列名
    row_count: int
    bad_rows: list[BadRow]

class RawBatch(BaseModel):
    batch_id: UUID
    platform: str
    data_type: str
    file_sha256: str             # 幂等：同文件重复导入直接返回
    file_name: str
    encoding: str
    sheets: list[RawSheet]
    declared_period: tuple[date, date] | None

class Record(BaseModel):
    record_id: str
    platform: str
    data_type: str
    platform_id: str
    canonical_spu_id: str | None
    fields: dict[str, Any]
    monetary: dict[str, Decimal]
    ts: datetime | None
    impl: str                    # "ingress.jd.v1"

class MappingReport(BaseModel):
    total: int
    row_ok: int
    row_bad: int
    unmapped_rate: float
    field_coverage: dict[str, float]
    pii_stats: dict[str, int]    # 每个 PII 列丢弃了多少
    warnings: list[str]
```

## 6. 一条真实数据走一遍（端到端示例）

```text
输入（京东评价导出，GBK，第 3 行是表头）：
  评价编号 | 商品名称 | 评价内容 | 评价星级 | 评价时间 | 买家昵称 | 商品价格

处理：
  1. 编码探测 → GBK
  2. 表头探测 → header_row=3
  3. 字段映射 → 评价编号→platform_id, 评价内容→content, 评价星级→rating, 评价时间→ts, 商品价格→spu_price；买家昵称→PII
  4. 类型清洗 → 星级"5"→5, 时间"2026-10-01"→datetime, 价格"￥1,299.00"→Decimal(1299.00)
  5. PII → 买家昵称丢弃，计数 +1
  6. 输出 Record（无任何 PII 字段）

输出：RawBatch + MappingReport{total, row_ok, row_bad, pii_stats: {"买家昵称": 5000}}
```

## 7. 开发步骤（勾选清单，约 9h）

- [ ] 7.1 `mapping.py` + 两份 `config/mappings/{taobao,jd}.yaml`——1.5h
- [ ] 7.2 `header.py` 表头探测——1.5h
- [ ] 7.3 `encoding.py` 编码探测 + 中文占比校验——1h
- [ ] 7.4 `cleaner.py` 四类清洗（金额/时间/评分/多图）——3h
- [ ] 7.5 `report.py` 坏行隔离 + MappingReport——1h
- [ ] 7.6 `pii.py` PII 丢弃 + 正文打码 + 单测——1h
- [ ] 7.7 `base.py` 串起管线 + `hub run p0` 命令 + 幂等（file_sha256）——1h

## 8. 交付物

- [ ] `ingress/` 全模块 + `config/mappings/{taobao,jd}.yaml`
- [ ] `make p0` 命令可用
- [ ] 坏行隔离文件 + MappingReport 可生成
- [ ] PII 丢弃有单测证明

## 9. 验收

```bash
make p0 FILE=data/raw/jd_review.xlsx PLATFORM=jd TYPE=review
# 期望：
#   1) 表头识别正确（header_row 输出 3）
#   2) row_ok + row_bad == 文件总数据行
#   3) 金额列是 Decimal（用 python 打印 type 验证）
#   4) pii_stats 里"买家昵称"计数 == 总行数
#   5) bad_rows 文件里能对应到具体的脏行
#   6) 同文件再跑一次 → 返回"已存在"，不重复导入（幂等）
```

全部通过 → 进入 04。

## 10. 常见坑（数据脏活翻车重灾区）

1. **表头不是第一行**：没做探测，直接把第一行当表头 → 后面的数据全错位。**先探测，后读数据**；
2. **GBK 静默乱码**：`pandas.read_csv` 默认 UTF-8，GBK 文件会直接报错或乱码。用 §4.2 的"先 UTF-8 后 GBK + 中文占比校验"；
3. **金额用 float**：`1299.00` 变 `1298.999...`，跨平台价格对比直接失真。**Decimal 是硬约束**；
4. **"好中差评"和星级同时存在**：淘宝常两者都有且可能不一致，必须定"星级优先 + 冲突计数"，否则下游评分统计是错的；
5. **Excel 日期序列号**：`read_excel` 会把日期变成 `45630.0` 这种浮点，直接当字符串就错了。要识别并还原；
6. **PII 写进了日志**：调试时 `print(row)` 把手机号打到日志，等于没脱敏。**PII 列从源头就 drop，日志中间件再兜一层**；
7. **坏行没留原始行**：隔离文件只写了"第 1234 行失败"但没写原始内容，事后无法排查。**保留原始行**。

---

**下一篇：04 M1 DQE 数据降噪**——规则轨 + ML 轨 + 决策融合 + 可插拔降级，跑出你简历的第一个真实数字（降噪率）
