// ══════════════════════════════════════════════════════════════
//  中文标签层（labels.ts）—— **唯一翻译入口**
// ══════════════════════════════════════════════════════════════
// **为什么有这个文件**（2026-11-02，用户实测反馈）：
//   我把后端的枚举值**原样**倒给了用户：
//     · 复盘页显示 `WAITING` / `INCONCLUSIVE` / `SIGNIFICANT` ✅ ✗
//     · 操作日志显示 `proposal.approve` / `data.ingest` ✅ ✗
//     · 通知里显示 `dead_letter` 三个字"死信"（用户说看不懂 ✅）
//   → **所有后端字段都要在这里翻成人话 + 一句解释** ✅
//
// **规矩**
//   页面上**不许再出现英文枚举**（除了"技术细节"折叠区 ✅）
//   每个标签带 `hint`：**鼠标放上去能看懂"这是什么"** ✅
//   未知值**不抛错**（后端加新枚举时前端不该白屏 ✅）→ `labelOf()` 兜底
// ══════════════════════════════════════════════════════════════

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'primary'

export interface Label {
  /** 给用户看的中文 */
  text: string
  /** 一句话解释（放 tooltip ✅）*/
  hint?: string
  /** 标签颜色 */
  tone?: Tone
}

// ══════════════════════════════════════════════════════════════
//  提案 / 待办 / 任务 状态
// ══════════════════════════════════════════════════════════════
export const STATUS: Record<string, Label> = {
  // --- action_proposal ---
  PROPOSED: {
    text: '待处理', tone: 'info',
    hint: '刚生成，还没提交给网关（所以还不能批准）',
  },
  PENDING_APPROVAL: {
    text: '待批准', tone: 'warning',
    hint: '已提交网关，等主管点头；批准后才会执行',
  },
  PENDING_HITL: {
    text: '需人工处理', tone: 'danger',
    hint: 'L3 高风险动作：系统不会自动做，只生成人工工单',
  },
  APPROVED: {
    text: '已批准', tone: 'success',
    hint: '主管同意了，正在或已经执行',
  },
  EXECUTED: {
    text: '已执行', tone: 'success',
    hint: '动作真的做了，而且执行后代码回查过一次',
  },
  REJECTED: {
    text: '已拒绝', tone: 'danger',
    hint: '主管不同意，没有执行',
  },
  SKIPPED: {
    text: '已跳过', tone: 'info',
    hint: '没有执行（比如缺依据、或测试数据）',
  },

  // --- hitl_task ---
  PENDING: { text: '待处理', tone: 'warning', hint: '在等人做决定' },
  EXPIRED: { text: '已过期', tone: 'info', hint: '等太久了，自动失效' },

  // --- pipeline_run（一条链）---
  QUEUED: { text: '排队中', tone: 'info', hint: '已进队列，等 worker 来捡' },
  RUNNING: { text: '运行中', tone: 'warning', hint: '正在跑' },
  DONE: { text: '完成', tone: 'success', hint: '全部阶段跑完' },
  DEGRADED: {
    text: '完成（降级）', tone: 'warning',
    hint: '跑完了，但某个阶段走了降级路径（比如视觉模型不可用）',
  },
  FAILED: { text: '失败', tone: 'danger', hint: '某个阶段报错，没跑完' },
  DEAD: {
    text: '失败待处理', tone: 'danger',
    hint: '重试 3 次仍失败，进了死信队列，需要人看一眼或重放',
  },

  // --- action_outcome ---
  WAITING: {
    text: '等观察窗口', tone: 'info',
    hint: '动作做了，要等 14 天才能判断有没有用',
  },

  // --- agent_run ---
  NEEDS_HUMAN: {
    text: '需人工判断', tone: 'warning',
    hint: 'agent 没能给出可靠结论（或 Critic 不通过），转给人',
  },
}

// ══════════════════════════════════════════════════════════════
//  复盘结论
// ══════════════════════════════════════════════════════════════
export const CONCLUSION: Record<string, Label> = {
  POSITIVE: {
    text: '有效', tone: 'success',
    hint: '效果量的置信区间**不跨 0**，方向是变好',
  },
  NEGATIVE: {
    text: '有害', tone: 'danger',
    hint: '效果量的置信区间**不跨 0**，方向是变差（要复盘为什么）',
  },
  INCONCLUSIVE: {
    text: '不能下结论', tone: 'warning',
    hint: '区间**跨了 0**（或样本太少）→ 数据还不足以说明有没有用',
  },
}

// ══════════════════════════════════════════════════════════════
//  复盘理由码
// ══════════════════════════════════════════════════════════════
export const REASON: Record<string, Label> = {
  SIGNIFICANT: { text: '统计显著', hint: '区间不跨 0，结论可信' },
  NOT_SIGNIFICANT: { text: '不显著', hint: '区间跨 0，看不出差异' },
  INSUFFICIENT_SAMPLES: { text: '样本太少', hint: '样本量不足，不敢下结论' },
  INSUFFICIENT_CONTROL: { text: '对照组不足', hint: '没有可比的对照商品' },
  PROMO_CONFOUNDED: { text: '撞大促', hint: '观察期里有大促，效果被污染了' },
  WINDOW_NOT_REACHED: { text: '观察期没到', hint: '还在等 14 天窗口' },
  NO_BASELINE: { text: '没有基期数据', hint: '动作前就没有可比的数据' },
}

// ══════════════════════════════════════════════════════════════
//  风险等级
// ══════════════════════════════════════════════════════════════
export const RISK: Record<string, Label> = {
  L0: { text: 'L0 · 只读', tone: 'success', hint: '纯查询，不改任何数据' },
  L1: { text: 'L1 · 低风险', tone: 'info', hint: '影响范围小（比如回复草稿），提交后可执行' },
  L2: { text: 'L2 · 中风险', tone: 'warning', hint: '会改线上内容（描述/价格）→ **需要主管批准**' },
  L3: { text: 'L3 · 高风险', tone: 'danger', hint: '影响钱或合规（改价/赔偿）→ **不自动执行，只转人工工单**' },
}

// ══════════════════════════════════════════════════════════════
//  工具名
// ══════════════════════════════════════════════════════════════
export const TOOL: Record<string, Label> = {
  // 写工具
  // ⚠️ 2026-11-04 改语义：回复差评是**客服的活**，运营批它没意义 ✅
  //    正确语义：**给客服生成一批任务**（哪些评价 + 统一话术 + 合规过审）
  //    运营批的是「要不要集中处理这波」，不是「亲自回复」✅
  // ⚠️ **历史动作**（2026-11-04 被 `cs.batch_task` 取代 ✅）
  //    保留只为兼容旧提案数据 ✅ 新提案不再用它
  'review.draft_reply': { text: '生成客服任务单（旧版）',
    hint: '历史动作，已被「生成客服批量任务」取代 ✅' },
  // **补两个运营真该决策的动作**（2026-11-04）
  'factory.feedback': { text: '反馈工厂',
    hint: '把问题反馈给工厂（内部动作，影响下一批货，不改线上内容）' },
  'cs.batch_task': { text: '生成客服批量任务',
    hint: '给客服一批集中处理任务（N 条差评 + 话术 + 优先级）' },
  'listing.update_desc': { text: '修改商品描述', hint: '改详情页文案（中风险，要批准）' },
  'listing.restore_desc': { text: '回滚商品描述', hint: '把描述改回上一次的版本（补偿用）' },
  'product.update_price': { text: '修改商品价格', hint: '改价格（L3 高风险，只生成工单）' },
  'catalog.query_price': { text: '查价格', hint: '只读' },
  // 读工具
  query_cluster: { text: '查问题簇', hint: '读这个簇的基本信息（条数/评分/涉及商品）' },
  query_reviews: { text: '查评价原文', hint: '读这个簇的代表性差评' },
  query_history: { text: '查历史战绩', hint: '读同类动作过去的效果' },
  query_product: { text: '查商品信息', hint: '读商品标题/价格/类目' },
  query_outcome: { text: '查复盘结论', hint: '读这个动作的 DID 结论' },
}

// ══════════════════════════════════════════════════════════════
//  问题类型
// ══════════════════════════════════════════════════════════════
export const TOPIC: Record<string, Label> = {
  quality: { text: '质量' },
  size: { text: '尺码' },
  logistics: { text: '物流' },
  service: { text: '服务' },
  color: { text: '色差' },
  price: { text: '价格' },
  desc_mismatch: { text: '描述不符' },
  misship: { text: '发错货' },
  other: { text: '其他' },
}

// ══════════════════════════════════════════════════════════════
//  优先级档
// ══════════════════════════════════════════════════════════════
export const TIER: Record<string, Label> = {
  P0: { text: 'P0 · 最高', tone: 'danger', hint: '生意影响最大，今天就该处理' },
  P1: { text: 'P1 · 高', tone: 'warning', hint: '影响明显，本周处理' },
  P2: { text: 'P2 · 中', tone: 'primary', hint: '可以晚点' },
  P3: { text: 'P3 · 低', tone: 'info', hint: '先记着' },
}

// ══════════════════════════════════════════════════════════════
//  审计动作（**操作日志那页用** ✅）
// ══════════════════════════════════════════════════════════════
export const ACTION: Record<string, Label> = {
  login: { text: '登录', hint: '有人登录了系统' },
  logout: { text: '登出', hint: '主动退出（服务端会立刻作废这个 token）' },
  'run.trigger': { text: '触发全链', hint: '把 M1→M11 整条链排进队列' },
  'proposal.approve': { text: '批准提案', hint: '主管点头 → 动作会真的执行' },
  'proposal.reject': { text: '拒绝提案', hint: '主管不同意 → 动作不执行' },
  'agent.adopt': { text: '采纳 agent 建议', hint: '把 agent 的方案变成正式提案' },
  'agent.reject': { text: '不采纳 agent 建议', hint: '只看审计，不改 agent 运行记录' },
  'data.ingest': { text: '导入数据表', hint: '上传了一个平台导出的 xlsx' },
  'data.ingest_images': { text: '导入图片包', hint: '上传了一个图片 zip，跑 M2 多模态' },
  'knowledge.create': { text: '新建知识条目', hint: '写了新口径（DRAFT，还不生效）' },
  'knowledge.publish': { text: '发布知识条目', hint: '口径发布 → 立刻注入 AI 提示词' },
  'retro.rerun': { text: '重新复盘', hint: '重跑一次 DID' },
  'dlq.replay': { text: '重放死信', hint: '把失败的任务重新入队（危险）' },
  'cloud.request': { text: '数据出网', hint: '把聚合数据发到云端（每次都会记一条）' },
}

// ══════════════════════════════════════════════════════════════
//  通知事件类型
// ══════════════════════════════════════════════════════════════
export const EVENT: Record<string, Label> = {
  'proposal.pending': {
    text: '有提案等你批', tone: 'warning',
    hint: '超过 1 天没人批的提案会提醒一次',
  },
  'retro.due': {
    text: '有动作到期该复盘', tone: 'primary',
    hint: '观察窗口（14 天）到了，可以算 DID 了',
  },
  dead_letter: {
    text: '有任务失败待处理', tone: 'danger',
    hint: '重试 3 次仍失败 → 进死信队列，需要人看一眼或重放',
  },
  'agent.done': {
    text: 'agent 需要人接手', tone: 'info',
    hint: 'agent 没给出可靠结论（或 Critic 不通过）',
  },
}

/** 通知渠道 */
export const CHANNEL: Record<string, Label> = {
  webhook: { text: '企业微信/飞书' },
  email: { text: '邮件' },
  web: { text: '站内' },
}

// ══════════════════════════════════════════════════════════════
//  数据类型 / 平台 / 图片缺陷
// ══════════════════════════════════════════════════════════════
export const DATA_TYPE: Record<string, Label> = {
  review: { text: '评价', hint: '评价原文 + 评分 + 买家秀图片地址' },
  product: { text: '商品', hint: '标题 / 价格 / 类目 / 库存' },
  sales: { text: '销售', hint: '日粒度：GMV / 销量 / UV / 退款' },
}

export const DEFECT: Record<string, Label> = {
  none: { text: '无缺陷', tone: 'success' },
  破损: { text: '破损', tone: 'danger' },
  开线: { text: '开线', tone: 'warning' },
  色差: { text: '色差', tone: 'warning' },
  污渍: { text: '污渍', tone: 'warning' },
  其他: { text: '其他', tone: 'info' },
}

// ══════════════════════════════════════════════════════════════
//  兜底取值
// ══════════════════════════════════════════════════════════════
/**
 * 从字典里取标签；**未知值不抛错**（后端加新枚举时前端不该白屏 ✅）
 * 未知值的兜底：**原样显示 + 提示"未翻译"**（方便我发现漏了 ✅）
 */
export function labelOf(dict: Record<string, Label>, key: string | null | undefined,
                        fallbackText?: string): Label {
  if (key == null || key === '') {
    return { text: fallbackText ?? '—', tone: 'info' }
  }
  const hit = dict[key]
  if (hit) return hit
  // 未翻译：**显示原值 + 灰色**，并 hint 提示
  return { text: key, tone: 'info', hint: `（未翻译的枚举值：${key}）` }
}

/** 只要中文（大多数地方够用 ✅）*/
export function textOf(dict: Record<string, Label>, key: string | null | undefined,
                       fallbackText?: string): string {
  return labelOf(dict, key, fallbackText).text
}
