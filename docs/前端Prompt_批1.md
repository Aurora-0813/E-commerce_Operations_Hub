# 前端生成 Prompt · 批 1（布局 + 登录 + 数据层 + 今日工作台）

> 说明：这是**改版后的 prompt**，可直接整段粘贴给模型。
> 批 2/3/4 用同一份 prompt，只改 §6「本批范围」。文末有 3 批的划分建议。

---

## 【第 0 段 · 最重要的一段，不要删】

后端已经**全部实现并跑起来了**，接口**不是你编的**：

- **接口清单与说明**：项目里的 `docs/API.md`（**43 个接口**，含每个接口的
  错误码、危险操作、返回示例）
- **机器可读的 OpenAPI**：`http://127.0.0.1:8080/api/openapi.json`
  （后端已在 8080 跑着；Swagger 在 `/api/docs`）

**硬性约束**：
1. **只允许调用 `docs/API.md` 里列出的 43 个接口**。**不准自己编路径**
   （不存在的接口会被后端 404，那不是"后端没做好"）。
2. **TypeScript 类型从 `openapi-typescript` 生成**，不要手写 43 个 interface：
   ```bash
   npx openapi-typescript http://127.0.0.1:8080/api/openapi.json -o src/types/api.ts
   ```
3. **禁止用 mock 数据**。页面没数据时走「空态」，**不许塞假数据让它好看**。
   我们的空态是**真的有**：`/api/notify` 可能 0 条、`/api/knowledge` 可能 0 条、
   `/api/brief` 会返回 `available: false`。**这些都是正常状态，要正确显示。**

---

## 【第 1 段 · 角色】

以一名资深 Vue3 前端架构师与 UI 工程师的视角工作。
你的产出会被一个**真实运营团队**每天使用，不是 demo：

- 他们会**看很久**（信息密度高但要能扫读）
- 他们**会点错**（危险操作要有二次确认）
- 他们**会没数据**（空态要能指导下一步）

---

## 【第 2 段 · 技术栈（固定）】

```
Vue 3 `<script setup lang="ts">` + TypeScript **strict（禁 any）**
Vite 5
Element Plus（含暗色主题）
**echarts + vue-echarts**（数据看板必须有图）
Pinia（只放：用户 / 平台上下文 / 应用设置）
Vue Router 4
axios（拦截器注入 Bearer、401 跳登录）
**openapi-typescript**（从 openapi.json 生成类型，产物 `src/types/api.ts`）
```

**明确不要**：
- ❌ **UnoCSS / Tailwind** —— 样式统一走 **SCSS 变量（编译期）+ CSS 自定义属性（运行期）**
  理由：Element Plus 自己的主题也是 CSS 变量，两套 utility 体系会打架，暗色主题要改两处。

**允许，但有约束**（这几条是产品方明确要的，别省略）：

| 允许 | 约束（**必须遵守**） |
| --- | --- |
| **渐变 / 轻动效 / AI 质感图标** | ① **只在「AI 产物」上用**（日报卡、agent 轨迹头部、依据高亮），**不要给每个卡片都加**<br>② 渐变**不能降低对比度**：文字必须过 WCAG AA（正文 ≥ 4.5:1）<br>③ 动效**不炫技**：不转圈、不呼吸灯、不做「AI 正在思考…」打字机<br>④ **图标仍以 Element Plus 为主**，AI 质感图标**最多 3 个**（Logo / 日报 / agent 轨迹） |
| **图表入场动画** | ① **时长 300~500ms**，`easing: cubicOut`<br>② **只在首次渲染播**：数据刷新 / 轮询更新时**不要重播**（否则每 2 秒抖一次 ✅ ✗）<br>③ **`prefers-reduced-motion: reduce` 时必须关掉**（系统设置优先于设计偏好 ✅）<br>④ 17 张里**只给 3 张主图**开动画（① P 档环形 / ⑪ GMV TopN / ⑫ DID 区间），其余不做 |

> **为什么加这些约束**：数据看板会被运营**每天开几十次**。
> 第一次的渐变很好看，第二十次就是干扰 ✅ —— 所以「允许」不等于「到处用」。

---

## 【第 3 段 · 视觉规范（严格照做）】

**整体**：现代 SaaS 后台。**浅色内容区 + 深色侧边栏（`#1f2d3d` 系）**，主色 `#409EFF`。
**支持一键切暗色**（`html.dark` + `element-plus/theme-chalk/dark/css-vars.css`
+ **ECharts 同步换主题**）。

| 项 | 值 |
| --- | --- |
| 卡片圆角 | `8px` |
| 轻阴影 | `0 2px 8px rgba(0,0,0,.06)` |
| 间距体系 | **8px 栅格**（8 / 16 / 24 / 32） |
| 页面标题 | `16px / 600` |
| 正文 | `14px` |
| 辅助文字 | `12px`，`#909399` |
| 表格 | 行高舒适（`48px`），**表头浅灰底** |
| **KPI 数字** | **`font-variant-numeric: tabular-nums`**（等宽数字，切换数据时不跳动） |

**交互细节**：侧边栏折叠动画（`width` 过渡 200ms）、菜单 hover/active 态、
标签页切换、面包屑、页面切换**淡入**过渡（150ms，不要花哨）。

**关于 AI 元素**（这条请精确理解）：

```
✅ **允许**（产品方要的）：淡渐变、轻动效、AI 质感图标
   —— 但**只在 AI 产物上用**（日报卡 / agent 轨迹头部 / 依据高亮），
   并遵守 §2 那张表里的 4 条约束（对比度 / 不炫技 / 图标 ≤3 个 / reduced-motion）

✅ **要突出**：**AI 的产物本身**
   · AI 日报的 3~5 条人话（带「这个数字来自哪」的可展开来源）
   · agent 的 **4 步轨迹 + 每步 token**
   · **每条建议能展开到原始评价高亮** ← 这是产品最大的卖点
   · Critic 的独立核对意见

❌ **不要**：给每个卡片都套渐变 / 发光边框 / 呼吸灯 / 「AI 正在思考…」打字机
   —— 第一次看是加分，第 20 次看是干扰 ✅

图标：**仍以 Element Plus 为主**，AI 质感图标最多 3 个（Logo / 日报 / agent 轨迹），
     不引入额外图标库。
```

---

## 【第 4 段 · 布局结构】

1. **左侧 Sidebar**
   - Logo + **递归多级折叠菜单**（支持图标）
   - 折叠后**只显示图标 + tooltip**
   - **`< 1200px` 自动收起**（用 `matchMedia`，**销毁时必须 `removeEventListener`**）
   - 侧边栏折叠状态要在 `localStorage` 里记住（刷新后保持）

2. **顶部 Navbar**
   - 折叠按钮、面包屑
   - **全局「平台 + 店铺」联动切换器** → 切换后写入 Pinia，**所有页面感知**
     （平台配色统一从 `src/config/platform.ts` 读，**同一平台全局同色**）
   - 时间范围选择器（今天 / 近 7 天 / 近 30 天 / 自定义）
   - 消息通知（**铃铛上的数字来自 `GET /api/notify` 的 `unread`** ✅）
   - 全屏、用户下拉（**登出要调 `POST /api/auth/logout`**，不能只清本地 token ✅）

3. **标签页 Tabs**
   - 右键菜单：关闭其他 / 关闭全部 / 刷新
   - 与 `keep-alive` 联动（`include` 用组件名，**不要用路由名**）
   - **登录页不进 Tabs**

4. **内容区**
   - 可滚动、带标题区
   - **页面级 loading / 空态 / 错误态**（见第 5 段）
   - 页面切换**淡入**过渡

5. **可配置常量**（抽到 `src/config/`，不要散落魔法值）
   - 侧边栏宽度（`240px` / 折叠 `64px`）
   - 是否固定头部
   - 主题色、圆角、阴影、间距

---

## 【第 5 段 · 数据层约定（**这段是新增的，也是最重要的**）】

### 5.1 axios 实例（`src/api/client.ts`）

```text
baseURL: import.meta.env.VITE_API_BASE（默认 http://127.0.0.1:8080）
请求拦截器：
  · 注入 `Authorization: Bearer <token>`（token 从 `stores/user` 取）
  · 注入 `X-Trace-Id`（前端生成，方便和后端日志对账）
响应拦截器：
  · **401 → 清 token + 跳 /login**（**token 只有 3 小时**，会过期 ✅）
  · 403 → 提示「权限不够」+ **不要跳登录**（是角色问题，不是登录问题 ✅）
  · 409 → **不要当错误弹红**（我们的 409 是幂等："已经有任务了" ✅）
       · `POST /api/runs` 409 带 `run_id` → **直接跳去轮询那个任务** ✅
       · `POST /api/handle` 409 带 `run_id` → 同上
       · `POST /api/knowledge/{id}/publish` 409 → 提示"已发布过" ✅
  · 422 → 字段校验失败（如"拒绝必须带原因"）→ **把 message 显示在表单上**
  · 503 → "服务暂时不可用"（Redis 连不上 ✅）
  · 所有错误都要显示 **`trace_id`**（后端每个错误都带 ✅）
```

### 5.2 三态组件（**必须有，因为我们的空态是真的**）

```text
components/PageLoading.vue    骨架屏，不要转圈（看板用骨架更像真实系统）
components/EmptyState.vue     空数据 + **"怎么让它有数据"的一句话**
  例：/api/notify 空 → "通知要跑 `hub notify --scan` 才会产生"
      /api/knowledge 空 → "还没有知识条目，点右上角新建"
      /api/brief 空 → "先跑一次 `hub brief` 生成日报"
components/ErrorState.vue     带 trace_id + "重试"按钮
```

### 5.3 异步任务（**`202` 不是完成**，这是最容易写错的地方）

```text
POST /api/runs 或 /api/handle → **202 + run_id**
   ↓ **必须轮询** GET /api/runs/{run_id} 直到 status ∈ {DONE, DEGRADED, FAILED, DEAD}
   ↓ DONE 之后：`params.agent_run_id` 才是**结果**的 id
      → 再 GET /api/handle/{agent_run_id} 拿轨迹
   ↓
要求：
   · 轮询间隔 2 秒，**上限 60 次**（2 分钟），超了提示"任务还在跑，去 /ops 看"
   · **组件销毁必须清定时器** ✅
   · DEAD 状态（死信）要提示"需人工 replay（在 /ops 页）"
```

### 5.4 上传（`POST /api/ingest`）

```text
multipart：file（xlsx ≤ 20MB）+ platform（taobao|jd）+ data_type（review|product|sales）
   ↓ 要做成"拖进来就导"
   ↓ **必须显示**：rows_ok / rows_bad / warnings
   ↓ `existed: true` → **不是错误**，提示"这个文件导过了（同一个 SHA256）"
   ↓ `rows_ok: 0` 且 `warnings[0]` 里有"platform 传错" → **红字高亮**那条 warning
```

---

## 【第 6 段 · 本批范围（**只做这些，不要做别的**）】

### 产出文件

```
src/api/client.ts                  axios 实例 + 拦截器
src/api/auth.ts                    login / logout / me
src/api/read.ts                    today / status / health / brief / eval/latest / notify（只做用到的）
src/types/api.ts                   openapi-typescript 生成
src/stores/app.ts                  侧边栏折叠 / 主题 / 时间范围
src/stores/user.ts                 token / 用户信息 / 角色
src/stores/platform.ts             平台 + 店铺上下文
src/config/platform.ts             平台配色（taobao/jd）
src/config/menu.ts                 菜单（**从 router 派生，单一来源**）
src/config/layout.ts               侧边栏宽度 / 是否固定头 / 主题色
src/router/index.ts                路由 + 守卫（未登录 → /login）
src/layouts/default/index.vue      主框架
src/layouts/components/Sidebar/index.vue
src/layouts/components/Navbar/index.vue
src/layouts/components/Navbar/PlatformSwitcher.vue
src/layouts/components/Tabs/index.vue
src/layouts/components/Breadcrumb/index.vue
src/components/PageLoading.vue     骨架屏
src/components/EmptyState.vue
src/components/ErrorState.vue
src/views/login/index.vue          0 号页：登录
src/views/dashboard/index.vue      1 号页：今日工作台
src/styles/variables.scss          SCSS 变量（编译期）
src/styles/theme.scss              Element Plus 主题覆盖 + 暗色（CSS 变量）
src/styles/index.scss              全局
vite.config.ts 片段                proxy / 别名 / openapi-typescript 脚本
package.json 新增依赖
```

### 1 号页「今日工作台」要展示什么（**对着 `docs/API.md` 来**）

```
数据源：GET /api/today + GET /api/brief + GET /api/charts/cluster-tiers
       + GET /api/charts/hitl-trend + GET /api/charts/observe-timeline
   ↓
① 顶部：AI 日报卡片（`brief.bullets` 3~5 条）
   · **每条要能展开"这个数字来自哪"**（`brief.inputs`）
   · `available: false` → **空态**："先跑一次 hub brief 生成日报"
② 中间左侧：P 档环形图（① `cluster-tiers`：P0 2 / P1 5 / P2 8 / P3 7）
③ 中间右侧：四张 KPI 卡（要做的 / 等窗口的 / 待批的 / 死信）
   · **数字用 tabular-nums**
④ 下部：两张小图
   · ② 待批趋势（`hitl-trend`）
   · ③ 等窗口甘特（`observe-timeline`）← **用 `bar` + `stack` 模拟，不引额外库**
⑤ 每条待办卡片**带动作按钮**（跳到对应提案详情 / 触发复盘 / 重放死信）
   · **按钮的可见性按角色**：`operator` **看不到"批准"** → 但**这不是安全措施**，
     API 会 403（UI 只是少让人点错 ✅）
```

---

## 【第 7 段 · 硬性要求】

1. **`<script setup lang="ts">`，TS strict，禁 `any`**（用 `unknown` + 类型收窄）
2. **所有尺寸/颜色走常量或 CSS 变量，禁止散落魔法值**
3. **平台配色统一从 `src/config/platform.ts` 读**，同一平台全局同色
4. **代码完整可直接运行，禁止「此处省略」**
5. **组件卸载必须清理**：定时器（轮询）/ `matchMedia` 监听 / `resize` 监听 / `echarts` 实例（`dispose()`）
6. **禁止 mock 数据**
7. **每个请求都要能失败**：写了 `try/catch`，错误走 `ErrorState` 或 `ElMessage`

---

## 【第 8 段 · 工作方式】

**先输出「实现计划」**，内容包括：

1. 文件清单（路径 + 每个文件一句话职责）
2. **`src/config/menu.ts` 和 `src/router/index.ts` 的单一来源方案**（谁派生谁）
3. **axios 拦截器的分支表**（401/403/409/422/503 分别怎么办）
4. **轮询的实现位置**（在 store 还是 composable）
5. **暗色主题怎么同时影响 Element Plus 和 ECharts**
6. **`keep-alive` 的 `include` 用组件名还是路由名**（给理由）

**我确认后你再逐个文件输出完整代码，每个代码块前注明完整文件路径。**

---

# 附：后续 3 批怎么用这份 prompt

只改 §6「本批范围」，其余 7 段**原样保留**（尤其第 0 段和第 5 段）。

| 批 | 本批范围（替换 §6） |
| --- | --- |
| **批 2** | 页 2 提案详情（**核心卖点：依据展开到原文高亮**）· 页 3 问题全景（图表最多：⑥⑦⑧⑨⑩） |
| **批 3** | 页 4 复盘报告（⑪⑫⑬⑭ **DID 区间图** —— 用 `custom` 系列画误差棒）· 页 5 Agent 任务（⑮⑯ 轨迹时间线 + 每步 token） |
| **批 4** | 页 6 操作日志 + 运维台（⑰⑱⑲⑳ + 触发全链/重放死信/**上传入口**）· 页 7 通知中心（㉑）· 页 8 知识库（㉒ DRAFT/PUBLISHED + `effective` 标记） |

**每批验收（都要过）**：
```text
① 页面能打开，无白屏、无 console 报错
② **数据是真的**（对着 `/api/*` 拉到的，不是 mock）
③ 每页至少**一个能成功的动作**
④ 断网 / 500 / 403 有明确提示（不白屏）
⑤ 空数据走空态，不是空白页
⑥ 切暗色后 Element Plus 和 ECharts **都变**（不能只变一半）
```
