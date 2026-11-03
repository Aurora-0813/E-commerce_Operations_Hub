// ══════════════════════════════════════════════════════════════
//  图片复核（views/images/index.vue）—— 2026-11-03 新增
// ══════════════════════════════════════════════════════════════
// **这一页解决什么**
//   AI 打的标**不能直接进赔偿决策** ✅
//   这一页把"低置信度 / 高风险"的图片**捞出来让人扫一眼** ✅
//
// **为什么值得给面试官看**
//   · 它不是"再画一张图"，而是**人机协同的闭环** ✅
//   · 复核结果落 `final_defect` → **合并 review_feature 时优先用它**
//   · 同时**就是下一次微调的标注集** ✅
// ══════════════════════════════════════════════════════════════
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ElButton, ElCard, ElMessage, ElOption, ElPagination, ElSelect, ElTable,
  ElTableColumn, ElTag,
} from 'element-plus'

import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageLoading from '@/components/PageLoading.vue'
import LabelTag from '@/components/LabelTag.vue'
import { DEFECT } from '@/config/labels'
import {
  DEFECT_OPTIONS, getImageStats, listImages, reviewImage,
  type Defect, type ImageAssetRow, type ImageStats,
} from '@/api/images'
import { useUserStore } from '@/stores/user'

const user = useUserStore()

const loading = ref(true)
const error = ref<unknown>(null)
const items = ref<ImageAssetRow[]>([])
const stats = ref<ImageStats | null>(null)

/** 筛选：默认只看待复核 ✅ */
const pendingOnly = ref(true)
const onlyLow = ref(false)
const defect = ref<string>('')
const page = ref(1)
const SIZE = 20

const shown = computed(() =>
  items.value.slice((page.value - 1) * SIZE, page.value * SIZE))

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [list, st] = await Promise.all([
      listImages({
        pending_only: pendingOnly.value,
        max_confidence: onlyLow.value ? 0.75 : undefined,
        defect: defect.value || undefined,
        limit: 200,
      }),
      getImageStats(),
    ])
    items.value = list
    stats.value = st
    page.value = 1
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

// ---------- 复核 ----------
const actingId = ref<string>('')
/** 展开的"改判定"行 */
const editingId = ref<string>('')
const editDefect = ref<Defect>('none')
const editNote = ref('')

function startEdit(row: ImageAssetRow): void {
  editingId.value = row.id
  editDefect.value = (row.final_defect ?? row.defect_type ?? 'none') as Defect
  editNote.value = ''
}

async function submitReview(row: ImageAssetRow, defectValue: Defect): Promise<void> {
  actingId.value = row.id
  try {
    await reviewImage(row.id, defectValue, editNote.value || undefined)
    ElMessage.success('已复核 ✅ 这条评价的合并结果已按人工判定更新')
    editingId.value = ''
    editNote.value = ''
    await load()
  } catch {
    // 401/403/404/422 都由 axios 拦截器提示 ✅
  } finally {
    actingId.value = ''
  }
}

/** 一键"确认模型结果"（最常见的操作 ✅）*/
async function confirmAsIs(row: ImageAssetRow): Promise<void> {
  await submitReview(row, (row.defect_type ?? 'none') as Defect)
}

const confidenceTone = (c: number | null): 'success' | 'warning' | 'danger' => {
  if (c == null) return 'warning'
  if (c >= 0.85) return 'success'
  if (c >= 0.7) return 'warning'
  return 'danger'
}

onMounted(load)
</script>

<template>
  <div class="imgrev">
    <header class="imgrev__head">
      <div>
        <h2 class="imgrev__title">图片复核</h2>
        <p class="imgrev__note">
          AI 打的标<b>不直接进赔偿决策</b> —— 低置信度 / 高风险缺陷在这里过一遍人眼。
          复核结果会<b>优先覆盖</b>模型结论，也<b>就是下次微调的标注数据</b> ✅
        </p>
      </div>
      <ElButton :loading="loading" @click="load()">刷新</ElButton>
    </header>

    <!-- 概况 -->
    <div v-if="stats" class="imgrev__kpis">
      <div class="imgrev__kpi">
        <div class="imgrev__kpi-label">图片总数</div>
        <div class="imgrev__kpi-value">{{ stats.total }}</div>
      </div>
      <div class="imgrev__kpi is-warn">
        <div class="imgrev__kpi-label">待复核</div>
        <div class="imgrev__kpi-value">{{ stats.pending }}</div>
      </div>
      <div class="imgrev__kpi is-danger">
        <div class="imgrev__kpi-label">低置信度（&lt; {{ stats.threshold }}）</div>
        <div class="imgrev__kpi-value">{{ stats.low_confidence }}</div>
      </div>
      <div class="imgrev__kpi is-danger">
        <div class="imgrev__kpi-label">高风险缺陷（破损/开线）</div>
        <div class="imgrev__kpi-value">{{ stats.high_risk }}</div>
      </div>
    </div>

    <!-- 筛选 -->
    <ElCard shadow="never" class="imgrev__filter">
      <div class="imgrev__filter-row">
        <ElSelect v-model="pendingOnly" class="imgrev__sel" @change="load()">
          <ElOption :value="true" label="只看待复核" />
          <ElOption :value="false" label="全部" />
        </ElSelect>
        <ElSelect v-model="onlyLow" class="imgrev__sel" @change="load()">
          <ElOption :value="false" label="不限置信度" />
          <ElOption :value="true" label="只看低置信度" />
        </ElSelect>
        <ElSelect v-model="defect" class="imgrev__sel" clearable placeholder="全部缺陷类型"
                  @change="load()">
          <ElOption v-for="d in DEFECT_OPTIONS" :key="d" :value="d" :label="d" />
        </ElSelect>
        <span class="imgrev__count">共 {{ items.length }} 条</span>
      </div>
    </ElCard>

    <PageLoading v-if="loading" :rows="6" />
    <ErrorState v-else-if="error" :error="error" @retry="load()" />
    <EmptyState v-else-if="!items.length"
                :description="pendingOnly ? '没有待复核的图片 ✅ 全部处理完了' : '还没有图片明细'"
                hint="图片明细来自「数据接入 → 图片包（zip）」的处理结果；先上传一个图片包 ✅" />

    <ElCard v-else shadow="never" class="imgrev__table-card">
      <ElTable :data="shown" size="small" row-key="id">
        <ElTableColumn label="原文件名" width="160">
          <template #default="{ row }">
            <code class="imgrev__file">{{ row.source_file ?? '—' }}</code>
            <div v-if="row.match_tier" class="imgrev__tier">匹配：{{ row.match_tier }}</div>
          </template>
        </ElTableColumn>

        <ElTableColumn label="评价" min-width="220">
          <template #default="{ row }">
            <div class="imgrev__pid">{{ row.platform_review_id ?? row.review_id.slice(0, 8) }}</div>
            <div class="imgrev__text">{{ row.review_text || '（无原文）' }}</div>
          </template>
        </ElTableColumn>

        <ElTableColumn label="模型判定" width="150">
          <template #default="{ row }">
            <LabelTag :dict="DEFECT" :value="row.defect_type" />
            <div class="imgrev__sub">
              严重度 {{ row.severity ?? '—' }} ·
              <ElTag :type="confidenceTone(row.confidence)" size="small" effect="plain">
                置信 {{ row.confidence == null ? '—' : row.confidence.toFixed(2) }}
              </ElTag>
            </div>
          </template>
        </ElTableColumn>

        <ElTableColumn label="人工判定" width="150">
          <template #default="{ row }">
            <template v-if="row.reviewed_at">
              <LabelTag :dict="DEFECT" :value="row.final_defect" />
              <div class="imgrev__sub">{{ row.reviewed_by }}</div>
              <div v-if="row.review_note" class="imgrev__sub">备注：{{ row.review_note }}</div>
            </template>
            <span v-else class="imgrev__sub">（未复核）</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <!-- 未复核 -->
            <template v-if="!row.reviewed_at && editingId !== row.id">
              <ElButton size="small" type="primary" plain :loading="actingId === row.id"
                        @click="(row: unknown) => confirmAsIs(row as ImageAssetRow)">
                确认模型结果
              </ElButton>
              <ElButton size="small" :disabled="actingId === row.id" @click="(row: unknown) => startEdit(row as ImageAssetRow)">
                改判定
              </ElButton>
            </template>

            <!-- 改判定（展开）-->
            <template v-else-if="editingId === row.id">
              <ElSelect v-model="editDefect" size="small" class="imgrev__sel-sm">
                <ElOption v-for="d in DEFECT_OPTIONS" :key="d" :value="d" :label="d" />
              </ElSelect>
              <input v-model="editNote" class="imgrev__note-input"
                     placeholder="备注（为什么改，最有价值 ✅）" />
              <div class="imgrev__edit-actions">
                <ElButton size="small" type="primary" :loading="actingId === row.id"
                          @click="(row: unknown) => submitReview(row as ImageAssetRow, editDefect)">
                  保存
                </ElButton>
                <ElButton size="small" text @click="editingId = ''">取消</ElButton>
              </div>
            </template>

            <!-- 已复核 -->
            <template v-else>
              <span class="imgrev__sub">✅ 已复核</span>
            </template>
          </template>
        </ElTableColumn>
      </ElTable>

      <div v-if="items.length > SIZE" class="imgrev__pager">
        <ElPagination v-model:current-page="page" :page-size="SIZE" :total="items.length"
                      layout="prev, pager, next, total" small background />
      </div>
    </ElCard>

    <div class="imgrev__hint">
      <span>阈值：置信度 &lt; {{ stats?.threshold ?? 0.75 }} 或 缺陷是「破损/开线」→ 建议人工看一眼</span>
      <span v-if="!user.hasRole('operator')" class="imgrev__hint-warn">
        ⚠️ 你的角色不能复核（需要 operator 及以上）
      </span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.imgrev {
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
    font-size: 18px;
    font-weight: 600;
  }

  &__note {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.7;
    color: var(--hub-text-secondary);
  }

  &__kpis {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: $space-md;

    @media (max-width: 1100px) {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  &__kpi {
    padding: $space-md;
    border-radius: $radius-base;
    border: 1px solid var(--hub-border-light);
    background: var(--hub-bg-card);

    &.is-warn {
      border-color: var(--el-color-warning-light-7);
    }

    &.is-danger {
      border-color: var(--el-color-danger-light-7);
    }
  }

  &__kpi-label {
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__kpi-value {
    margin-top: 4px;
    font-size: 22px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  &__filter-row {
    display: flex;
    align-items: center;
    gap: $space-sm;
    flex-wrap: wrap;
  }

  &__sel {
    width: 150px;
  }

  &__sel-sm {
    width: 92px;
    margin-right: 4px;
  }

  &__count {
    margin-left: auto;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__file {
    font-size: 11px;
    word-break: break-all;
  }

  &__tier {
    font-size: 10px;
    color: var(--hub-text-secondary);
  }

  &__pid {
    font-size: 12px;
    font-weight: 600;
  }

  &__text {
    font-size: 11px;
    line-height: 1.5;
    color: var(--hub-text-secondary);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  &__sub {
    margin-top: 2px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__note-input {
    width: 100%;
    margin-top: 4px;
    padding: 2px 6px;
    font-size: 11px;
    border: 1px solid var(--hub-border-light);
    border-radius: 4px;
    background: var(--hub-bg-card);
    color: var(--hub-text-primary);
  }

  &__edit-actions {
    display: flex;
    gap: 4px;
    margin-top: 4px;
  }

  &__pager {
    display: flex;
    justify-content: flex-end;
    margin-top: $space-sm;
  }

  &__hint {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__hint-warn {
    color: var(--el-color-warning);
  }
}
</style>
