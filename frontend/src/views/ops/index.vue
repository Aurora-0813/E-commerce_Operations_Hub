<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  6 号页：运维台（⑰⑱⑲⑳ + 上传 + 死信重放 + 触发全链）
//  ⚠️ 这一页**危险操作最多** —— 全部要二次确认 ✅
//  ⚠️ `/api/health` 返回的是**裸数组**（不是 {checks}）—— 我按真实结构解析 ✅
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref } from 'vue'
import {
  ElButton, ElCard, ElMessage, ElMessageBox, ElOption, ElSelect, ElTable,
  ElTableColumn, ElTag, ElUpload,
} from 'element-plus'
import type { UploadRawFile } from 'element-plus'
import { Refresh, Upload as UploadIcon } from '@element-plus/icons-vue'

import http from '@/api/client'
import { getAudit, type AuditRow } from '@/api/data'
import { ingestFile, postRun, replayDlq, type IngestResult } from '@/api/write'
import { ApiError } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'

// ---------- 日志 ----------
const auditLoading = ref(true)
const auditError = ref<unknown>(null)
const audits = ref<AuditRow[]>([])
const auditTotal = ref(0)
const auditAction = ref('')

async function loadAudit(): Promise<void> {
  auditLoading.value = true
  auditError.value = null
  try {
    const d = await getAudit({ limit: 30, action: auditAction.value || undefined })
    audits.value = d.items
    auditTotal.value = d.total
  } catch (e) {
    auditError.value = e
  } finally {
    auditLoading.value = false
  }
}

// ---------- 健康 / 门禁 ----------
const health = ref<Array<{ name?: string; ok?: boolean; detail?: string } | string[]>>([])
const evalInfo = ref<{ available: boolean; data?: { run_id: string; hard: number[]; soft: number[] } } | null>(null)

async function loadObs(): Promise<void> {
  try {
    const { data } = await http.get<unknown>('/api/health')
    health.value = Array.isArray(data)
      ? (data as Array<{ name?: string; ok?: boolean } | string[]>)
      : ((data as { checks?: unknown }).checks as typeof health.value) ?? []
  } catch {
    health.value = []
  }
  try {
    const { data } = await http.get<typeof evalInfo.value>('/api/eval/latest')
    evalInfo.value = data
  } catch {
    evalInfo.value = null
  }
}

onMounted(() => {
  void loadAudit()
  void loadObs()
})

// ---------- 上传 ----------
const upPlatform = ref<'taobao' | 'jd'>('jd')
const upType = ref<'review' | 'product' | 'sales'>('sales')
const uploading = ref(false)
const uploadPct = ref(0)
const lastIngest = ref<IngestResult | null>(null)

/** ⚠️ 前端只做**基本校验**（扩展名 + 大小）；**平台是否正确由后端 rows_ok/rows_bad 告诉** ✅ */
function beforeUpload(file: UploadRawFile): boolean {
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
    ElMessage.error('只接受 .xlsx（平台导出的都是 xlsx）')
    return false
  }
  const MAX = 20 * 1024 * 1024
  if (file.size > MAX) {
    ElMessage.error(`文件 ${(file.size / 1024 / 1024).toFixed(1)}MB，超过 20MB 上限`)
    return false
  }
  return true
}

async function doUpload(file: UploadRawFile): Promise<void> {
  uploading.value = true
  uploadPct.value = 0
  lastIngest.value = null
  try {
    const r = await ingestFile(file, upPlatform.value, upType.value,
                              (p) => { uploadPct.value = p })
    lastIngest.value = r
    if (r.existed) {
      ElMessage.info('这个文件导过了（同一个 SHA256），没重复写 ✅')
    } else if (r.rows_ok === 0) {
      ElMessage.warning('解析完成但一行都没入库 —— 看下面的 warnings ✅')
    } else {
      ElMessage.success(`已入库 ${r.rows_ok} 条 ✅`)
    }
  } catch (e) {
    if (e instanceof ApiError) ElMessage.error(e.message)
  } finally {
    uploading.value = false
  }
}

// ---------- 危险操作 ----------
const running = ref(false)

async function triggerFullPipeline(): Promise<void> {
  await ElMessageBox.confirm(
    '会真的跑一次全链（约 710 秒，占 GPU）。确定吗？',
    '触发全链', { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' },
  ).catch(() => Promise.reject(new Error('cancel')))
  running.value = true
  try {
    const r = await postRun('full')
    ElMessage.success(`已入队：run ${r.run_id.slice(0, 8)} ✅（这会跑 710 秒）`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.runId) {
      ElMessage.info(`已经有一条在跑了：${e.runId.slice(0, 8)} ✅`)
    }
  } finally {
    running.value = false
  }
}

const dlqPrefix = ref('')
const replaying = ref(false)
async function doReplay(): Promise<void> {
  if (dlqPrefix.value.trim().length < 4) {
    ElMessage.warning('至少输入 4 位 run id 前缀')
    return
  }
  await ElMessageBox.confirm(
    `会把这个前缀匹配到的死信重新入队：${dlqPrefix.value}。可能重复写数据，确定吗？`,
    '重放死信（危险）', { type: 'error', confirmButtonText: '确定重放', cancelButtonText: '取消' },
  ).catch(() => Promise.reject(new Error('cancel')))
  replaying.value = true
  try {
    const r = await replayDlq(dlqPrefix.value.trim())
    ElMessage.success(r.ok ? '已重新入队 ✅' : '没找到匹配的死信')
  } catch (e) {
    if (e instanceof ApiError) ElMessage.error(e.message)
  } finally {
    replaying.value = false
  }
}

// ---------- 展示 ----------
const healthOk = computed(() => {
  const arr = health.value.filter((x) => !Array.isArray(x)) as Array<{ ok?: boolean }>
  if (!arr.length) return null
  return arr.every((x) => x.ok !== false)
})
const evalPassed = computed(() => {
  const d = evalInfo.value?.data
  if (!d) return null
  return { hard: d.hard.filter((x) => x === 1).length, hardTotal: d.hard.length,
           soft: d.soft.filter((x) => x === 1).length, softTotal: d.soft.length }
})
</script>

<template>
  <div class="ops">
    <div class="ops__head">
      <div>
        <h2 class="ops__title">运维台</h2>
        <div class="ops__sub">工程视角：健康 / 门禁 / 日志 / 上传 / 危险操作</div>
      </div>
      <ElButton :icon="Refresh" :loading="auditLoading" @click="loadAudit(); loadObs()">刷新</ElButton>
    </div>

    <!-- ═══════ 健康 + 门禁 ═══════ -->
    <div class="ops__row">
      <ElCard shadow="never">
        <template #header><span class="ops__card-title">⑲ 系统健康</span></template>
        <div v-if="healthOk === null" class="ops__muted">没有健康数据</div>
        <div v-else class="ops__kv">
          <ElTag :type="healthOk ? 'success' : 'danger'" effect="dark">
            {{ healthOk ? '正常 ✅' : '有异常 ⚠️' }}
          </ElTag>
          <span class="ops__muted">{{ health.length }} 项检查</span>
        </div>
      </ElCard>

      <ElCard shadow="never">
        <template #header><span class="ops__card-title">⑳ 最近门禁</span></template>
        <div v-if="!evalPassed" class="ops__muted">还没跑过 eval</div>
        <div v-else class="ops__kv">
          <ElTag :type="evalPassed.hard === evalPassed.hardTotal ? 'success' : 'danger'">
            硬门禁 {{ evalPassed.hard }}/{{ evalPassed.hardTotal }}
          </ElTag>
          <ElTag type="info" effect="plain">
            软门禁 {{ evalPassed.soft }}/{{ evalPassed.softTotal }}
          </ElTag>
        </div>
      </ElCard>
    </div>

    <!-- ═══════ 上传 ═══════ -->
    <ElCard shadow="never">
      <template #header>
        <span class="ops__card-title">
          上传平台导出文件
          <span class="ops__muted">（≤20MB · 平台必须和文件对得上）</span>
        </span>
      </template>

      <div class="ops__upload-form">
        <ElSelect v-model="upPlatform" class="ops__sel">
          <ElOption value="taobao" label="淘宝" />
          <ElOption value="jd" label="京东" />
        </ElSelect>
        <ElSelect v-model="upType" class="ops__sel">
          <ElOption value="review" label="评价" />
          <ElOption value="product" label="商品" />
          <ElOption value="sales" label="销售" />
        </ElSelect>
        <ElUpload :auto-upload="false" :show-file-list="false" :before-upload="beforeUpload"
                  :on-change="(f: { raw?: UploadRawFile }) => f.raw && doUpload(f.raw)"
                  accept=".xlsx,.xlsm" :disabled="uploading">
          <ElButton type="primary" :icon="UploadIcon" :loading="uploading">
            选择 xlsx 上传
          </ElButton>
        </ElUpload>
        <span v-if="uploading" class="ops__muted">上传中 {{ uploadPct }}%</span>
      </div>

      <!-- 上传结果 -->
      <div v-if="lastIngest" class="ops__ingest">
        <div class="ops__ingest-row">
          <ElTag :type="lastIngest.rows_ok > 0 ? 'success' : 'warning'" effect="dark">
            {{ lastIngest.existed ? '已导过' : `入库 ${lastIngest.rows_ok} 条` }}
          </ElTag>
          <span>文件 {{ lastIngest.file }}</span>
          <span>· {{ lastIngest.platform }}/{{ lastIngest.data_type }}</span>
          <span>· 坏行 {{ lastIngest.rows_bad }}</span>
          <span>· {{ (lastIngest.bytes / 1024).toFixed(0) }}KB</span>
        </div>
        <!-- 🚨 平台传错时 warnings 第一条就是人话 -->
        <div v-if="lastIngest.warnings?.length" class="ops__warn">
          <div v-for="(w, i) in lastIngest.warnings" :key="i">{{ w }}</div>
        </div>
        <div class="ops__muted">
          ⚠️ 上传后**不会自动重跑全链** —— 要再点下面的「触发全链」✅
        </div>
      </div>
    </ElCard>

    <!-- ═══════ 危险操作 ═══════ -->
    <ElCard shadow="never" class="ops__danger">
      <template #header>
        <span class="ops__card-title ops__card-title--danger">危险操作（全部要二次确认 ✅）</span>
      </template>

      <div class="ops__danger-row">
        <div>
          <div class="ops__danger-name">触发全链</div>
          <div class="ops__muted">跑一次 M1→M11（约 710 秒，占 GPU）</div>
        </div>
        <ElButton type="warning" :loading="running" @click="triggerFullPipeline()">
          触发
        </ElButton>
      </div>

      <div class="ops__danger-row">
        <div class="ops__danger-flex">
          <div>
            <div class="ops__danger-name">重放死信</div>
            <div class="ops__muted">把失败的任务重新入队（**可能重复写数据**）</div>
          </div>
          <el-input v-model="dlqPrefix" placeholder="run id 前缀（≥4 位）"
                    class="ops__danger-input" />
        </div>
        <ElButton type="danger" :loading="replaying" @click="doReplay()">重放</ElButton>
      </div>
    </ElCard>

    <!-- ═══════ 日志 ═══════ -->
    <ElCard shadow="never">
      <template #header>
        <div class="ops__card-head">
          <span class="ops__card-title">⑰ 操作日志</span>
          <ElSelect v-model="auditAction" size="small" clearable placeholder="全部动作"
                    class="ops__sel-sm" @change="loadAudit()">
            <ElOption value="proposal.approve" label="批准提案" />
            <ElOption value="proposal.reject" label="拒绝提案" />
            <ElOption value="agent.adopt" label="采纳 agent" />
            <ElOption value="knowledge.publish" label="发布知识" />
            <ElOption value="data.ingest" label="上传数据" />
            <ElOption value="dlq.replay" label="重放死信" />
          </ElSelect>
          <span class="ops__muted">共 {{ auditTotal }} 条</span>
        </div>
      </template>

      <ErrorState v-if="auditError" :error="auditError" @retry="loadAudit()" />

      <EmptyState v-else-if="!auditLoading && !audits.length" compact
                  description="还没有操作日志" />

      <ElTable v-else v-loading="auditLoading" :data="audits" size="small" stripe>
        <ElTableColumn prop="at" label="时间" width="150">
          <template #default="{ row }">
            {{ String(row.at).slice(0, 19).replace('T', ' ') }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="actor" label="谁" width="180" show-overflow-tooltip />
        <ElTableColumn prop="action" label="动作" width="180" />
        <ElTableColumn prop="resource" label="对象" min-width="200" show-overflow-tooltip />
        <ElTableColumn prop="ip" label="IP" width="120" />
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped lang="scss">
.ops {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
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
  }

  &__row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: $space-md;
  }

  &__card-title {
    font-size: 13px;
    font-weight: 600;

    &--danger {
      color: var(--el-color-danger);
    }
  }

  &__card-head {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__muted {
    font-size: 11px;
    color: var(--hub-text-secondary);
    font-weight: 400;
  }

  &__kv {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__upload-form {
    display: flex;
    align-items: center;
    gap: $space-sm;
    flex-wrap: wrap;
  }

  &__sel {
    width: 110px;
  }

  &__sel-sm {
    width: 150px;
  }

  &__ingest {
    margin-top: $space-md;
    padding: 10px 12px;
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);
  }

  &__ingest-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 12px;
  }

  &__warn {
    margin-top: 6px;
    padding: 8px 10px;
    border-left: 3px solid var(--el-color-warning);
    border-radius: 0 $radius-sm $radius-sm 0;
    background: var(--el-color-warning-light-9);
    font-size: 12px;
    line-height: 1.7;
    color: var(--el-color-warning-dark-2);
  }

  &__danger {
    border-color: var(--el-color-danger-light-7);
  }

  &__danger-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-md;
    padding: 10px 0;

    & + & {
      border-top: 1px dashed var(--hub-border-light);
    }
  }

  &__danger-flex {
    display: flex;
    align-items: center;
    gap: $space-md;
    flex: 1 1 auto;
    flex-wrap: wrap;
  }

  &__danger-name {
    font-size: 13px;
    font-weight: 600;
  }

  &__danger-input {
    width: 240px;
  }

  @media (max-width: 1100px) {
    &__row {
      grid-template-columns: 1fr;
    }
  }
}
</style>
