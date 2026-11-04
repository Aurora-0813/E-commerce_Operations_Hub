<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  待批提案列表
//  ⚠️ 列表只给摘要（后端 `/api/proposals` 就是这样 ✅）→ 详情走 `/proposals/{id}`
//  ⚠️ **operator 不显示「批准」按钮** ✅ —— 但这不是安全措施，API 会 403
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElButton, ElCard, ElMessage, ElOption, ElPagination, ElSelect,
  ElTable, ElTableColumn, ElAlert } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import { getProposals, type ProposalRow } from '@/api/data'
import { approveProposal } from '@/api/write'
import { ApiError } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import LabelTag from '@/components/LabelTag.vue'
import { RISK, STATUS, TOOL, TOPIC } from '@/config/labels'
import ErrorState from '@/components/ErrorState.vue'
import { useUserStore } from '@/stores/user'

const router = useRouter()
// **按簇筛**（2026-11-04）：从「问题明细」点「看提案」跳过来 ✅
const route = useRoute()
const clusterFilter = computed(() => String(route.query.cluster_id ?? ''))
const user = useUserStore()

const loading = ref(true)
const error = ref<unknown>(null)
const rows = ref<ProposalRow[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const status = ref<string>('')

const canApprove = computed(() => user.hasRole('supervisor'))

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const d = await getProposals({
      limit: size.value,
      offset: (page.value - 1) * size.value,
      status: status.value || undefined,
      // 从簇明细跳过来时只显示这个簇的提案 ✅
      cluster_id: clusterFilter.value || undefined,
    })
    rows.value = d.items
    total.value = d.total
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
watch([page, status], () => void load())
watch(size, () => { page.value = 1; void load() })

// ---------- 展示 ----------

// ---------- 快捷批准（只有 supervisor 显示）----------
const actingId = ref('')
async function quickApprove(row: ProposalRow): Promise<void> {
  actingId.value = row.id
  try {
    await approveProposal(row.id, true)
    ElMessage.success('已批准')
    await load()
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 404) ElMessage.info('这条没有待批任务（可能已批过）')
      // 403 已由拦截器提示"权限不够" ✅
    }
  } finally {
    actingId.value = ''
  }
}

function openDetail(row: ProposalRow): void {
  void router.push(`/proposals/${row.id}`)
}

// ══════════════════════════════════════════════════════════════
//  **表头筛选**（2026-11-04 加）
// ══════════════════════════════════════════════════════════════
// 用户："能不能在题头上添加筛选功能"
//   → 用 Element 原生的 `filters` + `filter-method`（点表头漏斗图标 ✅）
//   → 选项来自 `labels.ts` 的字典，**和后端枚举一一对应** ✅
const toOptions = (dict: Record<string, { text: string }>) =>
  Object.entries(dict).map(([value, v]) => ({ text: v.text, value }))

const toolFilters = computed(() => toOptions(TOOL))
const riskFilters = computed(() => toOptions(RISK))
const statusFilters = computed(() => toOptions(STATUS))
const topicFilters = computed(() => toOptions(TOPIC))

// `filter-method` 的签名是 (value, row, column) => boolean ✅
const byTool = (v: string, row: ProposalRow): boolean => row.tool_name === v
const byRisk = (v: string, row: ProposalRow): boolean => row.risk_level === v
const byStatus = (v: string, row: ProposalRow): boolean => row.status === v
const byTopic = (v: string, row: ProposalRow): boolean => row.topic === v

// **簇筛选的显示与清除**（2026-11-04 加）
//   问题：从「簇明细 → 看提案」跳过来带 `?cluster_id=` ✅
//   但页面**没有任何提示** ✅ → 用户以为"提案只剩一条了" ✅ ✗


/** 清除簇筛选：回到 `/proposals`（不带 query ✅）*/
function clearClusterFilter(): void {
  void router.replace({ path: '/proposals' })
}


</script>

<template>
  <div class="plist">
    <div class="plist__head">
      <div>
        <h2 class="plist__title">待批提案</h2>
        <div class="plist__sub">
          共 <b>{{ total }}</b> 条
          <!-- **角色提示改成正确的**（2026-11-04）：
               原来写死「看不到批准按钮（API 会 403）」 ✅ ✗
               但 operator **能批 L2**（审批权限跟着工具走 ✅）
               L3 才必须 supervisor ✅ -->
          <span class="plist__role-hint">
            · 你是{{ user.role === 'supervisor' ? '「运营主管」' :
                    user.role === 'operator' ? '「运营专员」' : '「' + user.role + '」' }}
            <template v-if="user.role === 'operator'">：L1/L2 能批，L3（改价）需主管</template>
            <template v-else-if="user.role === 'supervisor'">：L1/L2/L3 都能批</template>
          </span>
        </div>
      </div>
      <div class="plist__actions">
        <ElSelect v-model="status" size="default" placeholder="全部状态" clearable
                  class="plist__filter">
          <ElOption value="PROPOSED" label="待处理" />
          <ElOption value="PENDING_APPROVAL" label="待批准" />
          <ElOption value="EXECUTED" label="已执行" />
          <ElOption value="REJECTED" label="已拒绝" />
        </ElSelect>
        <ElButton :icon="Refresh" :loading="loading" @click="load">刷新</ElButton>
      </div>
    </div>

      <!-- ⚠️ 2026-11-04 加：把「为什么只有 6 条」说清楚 ✅ -->
      <!-- **簇筛选提示条**（2026-11-04 加）
           从「问题分析 → 簇明细 → 看提案」跳过来会带 `?cluster_id=`
           → 原来**没有提示** ✅ 用户以为"提案就剩一条了" ✅ ✗
           → 现在明确显示 + 一键清除 ✅ -->
      <div v-if="clusterFilter" class="props__filter-bar">
        <span>
          <b>已按问题簇筛选</b> —— 只显示「从簇明细点「看提案」跳过来」的那一簇的提案
        </span>
        <ElButton link type="primary" @click="clearClusterFilter()">看全部提案</ElButton>
      </div>

      <ElAlert type="info" :closable="false" show-icon class="plist__rule">
        <template #title>
          只为 <b>P0 特急 / P1 急</b> 的问题生成提案 ✅
        </template>
        <div class="plist__rule-body">
          P2 普通 / P3 不急 的问题按规则<b>暂不处理</b> ——
          处理它们会占运营工时，收益不如先解决 P0/P1。
          想看全部问题，去「问题分析 → 簇明细」✅
        </div>
      </ElAlert>
    <ErrorState v-if="error" :error="error" @retry="load" />

    <ElCard v-else shadow="never">
      <EmptyState v-if="!loading && !rows.length"
                  description="没有提案"
                  hint="提案由 M5 从 P0/P1 簇生成；也可以让 agent 处理某个簇后采纳"
                  command="hub propose" />

      <ElTable v-else v-loading="loading" :data="rows" size="small" stripe
               @row-click="(row: unknown) => openDetail(row as ProposalRow)">
        <!-- 表头筛选（2026-11-04 加）：每列点漏斗图标就能筛 ✅ -->
        <ElTableColumn label="推荐动作" min-width="150"
                       :filters="toolFilters" :filter-method="byTool">
          <template #default="{ row }">
            <LabelTag :dict="TOOL" :value="row.tool_name" text-only />
          </template>
        </ElTableColumn>
        <ElTableColumn label="风险" width="110"
                       :filters="riskFilters" :filter-method="byRisk">
          <template #default="{ row }">
            <LabelTag :dict="RISK" :value="row.risk_level" effect="dark" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="110"
                       :filters="statusFilters" :filter-method="byStatus">
          <template #default="{ row }">
            <LabelTag :dict="STATUS" :value="row.status" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="问题类型" width="100"
                       :filters="topicFilters" :filter-method="byTopic">
          <template #default="{ row }">
            <LabelTag :dict="TOPIC" :value="row.topic" text-only />
          </template>
        </ElTableColumn>
        <ElTableColumn prop="rationale" label="理由" min-width="260" show-overflow-tooltip />
        <ElTableColumn label="创建时间" width="140">
          <template #default="{ row }">
            {{ new Date(row.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <ElButton size="small" text type="primary" @click.stop="openDetail(row as ProposalRow)">
              详情
            </ElButton>
            <!-- 只有 supervisor 看得到批准 -->
            <ElButton v-if="canApprove && row.status === 'PENDING_APPROVAL'"
                      size="small" type="success" :loading="actingId === row.id"
                      @click.stop="quickApprove(row as ProposalRow)">
              批准
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>

      <div v-if="total > size" class="plist__pager">
        <ElPagination v-model:current-page="page" v-model:page-size="size"
                      :total="total" :page-sizes="[20, 50, 100]"
                      layout="total, sizes, prev, pager, next" background small />
      </div>
    </ElCard>
  </div>
</template>

<style scoped lang="scss">
.plist {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-md;
  }

  &__title {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--hub-text-secondary);

    b {
      color: var(--hub-text-primary);
      @include tabular-nums;
    }
  }

  &__role-hint {
    color: var(--el-color-warning);
  }

  &__actions {
    display: flex;
    gap: $space-sm;
  }

  &__filter {
    width: 140px;
  }

  &__tool {
    font-size: 12px;
  }

  &__pager {
    display: flex;
    justify-content: flex-end;
    margin-top: $space-md;
  }

  :deep(.el-table__row) {
    cursor: pointer;
  }
}

.props {
  &__filter-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-sm;
    padding: $space-sm $space-md;
    margin-bottom: $space-sm;
    font-size: 12px;
    color: var(--hub-text-secondary);
    background: var(--el-color-primary-light-9);
    border-radius: $radius-base;
  }
}
</style>
