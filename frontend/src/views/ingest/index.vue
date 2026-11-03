<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  数据接入（独立页）—— 2026-11-01 从「运维台」拆出来的
//  **为什么拆**：上传是运营的**日常操作**（每周导一次），
//    不该和"死信重放（危险）"挤在"运维台"里，也不该藏在侧边栏最后一项 ✅
//  接口：POST /api/ingest（multipart，≤20MB）
//  ⚠️ 平台必须和文件对得上 —— 传错时 `rows_ok` 会是 0，`warnings[0]` 会直接说是平台问题 ✅
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref } from 'vue'
import {
  ElAlert, ElCard, ElMessage, ElOption, ElSelect, ElTable,
  ElTableColumn, ElTag, ElUpload,
} from 'element-plus'
import type { UploadRawFile } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'

import http from '@/api/client'
import { ingestFile, type IngestResult } from '@/api/write'
import ImageIngest from './ImageIngest.vue'
import { ApiError } from '@/api/client'

/** 当前 tab：xlsx（数据表）/ images（图片包）✅ */
const mode = ref<'xlsx' | 'images'>('xlsx')

// ---------- 选择 ----------
const platform = ref<'taobao' | 'jd'>('jd')
const dataType = ref<'review' | 'product' | 'sales'>('sales')

// ---------- 上传 ----------
const uploading = ref(false)
const pct = ref(0)
const result = ref<IngestResult | null>(null)

/**
 * 兼容两种 warnings 形状（2026-11-03 ✅）
 *   · 新：`{code, message, hint}` 对象
 *   · 旧：纯字符串（后端没升级时也能显示 ✅）
 */
function msgOf(w: unknown): string {
  if (typeof w === 'string') return w
  const o = w as { message?: string }
  return o?.message ?? String(w)
}

function hintOf(w: unknown): string {
  if (typeof w === 'string') return ''
  const o = w as { hint?: string }
  return o?.hint ?? ''
}
const errorMsg = ref('')
/** 上传历史（本次会话，不落库 ✅）*/
const history = ref<IngestResult[]>([])

function beforeUpload(file: UploadRawFile): boolean {
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
    ElMessage.error('只接受 .xlsx（平台导出的都是 xlsx ✅）')
    return false
  }
  const MAX = 20 * 1024 * 1024
  if (file.size > MAX) {
    ElMessage.error(`文件 ${(file.size / 1024 / 1024).toFixed(1)}MB，超过 20MB 上限 ✅`)
    return false
  }
  return true
}

async function doUpload(file: UploadRawFile): Promise<void> {
  uploading.value = true
  pct.value = 0
  result.value = null
  errorMsg.value = ''
  try {
    const r = await ingestFile(file, platform.value, dataType.value, (p) => { pct.value = p })
    result.value = r
    history.value.unshift(r)
    if (r.existed) {
      ElMessage.info('这个文件导过了（同一个 SHA256），没重复写 ✅')
    } else if (r.rows_ok === 0) {
      ElMessage.warning('解析完成但**一行都没入库** —— 看下面的 warnings ✅')
    } else {
      ElMessage.success(`已入库 ${r.rows_ok} 条 ✅`)
    }
  } catch (e) {
    if (e instanceof ApiError) {
      errorMsg.value = e.message
      ElMessage.error(e.message)
    }
  } finally {
    uploading.value = false
  }
}

// ---------- 当前数据现状（让用户知道"导进来之后库里有啥"）----------
interface StatusSection { title?: string; items?: Array<{ label?: string; value?: unknown }> }
const status = ref<StatusSection[]>([])
async function loadStatus(): Promise<void> {
  try {
    const { data } = await http.get<{ sections?: StatusSection[] }>('/api/status')
    status.value = data.sections ?? []
  } catch {
    status.value = []
  }
}
onMounted(() => void loadStatus())

/** 从 status 里挑几个和"数据量"相关的（**字段名可能变，用宽松匹配 ✅**）*/
const dataCounts = computed(() => {
  const out: Array<{ label: string; value: string }> = []
  for (const sec of status.value) {
    for (const it of sec.items ?? []) {
      const label = String(it.label ?? '')
      if (/评价|商品|销售|簇|记录|批次/.test(label)) {
        out.push({ label, value: String(it.value ?? '—') })
      }
    }
  }
  return out.slice(0, 8)
})

const TYPES = [
  { value: 'review' as const, label: '评价', hint: '评价原文 + 评分 + 买家秀 URL' },
  { value: 'product' as const, label: '商品', hint: '商品标题 / 价格 / 类目' },
  { value: 'sales' as const, label: '销售', hint: '日粒度：GMV / 销量 / UV / 退款' },
]
</script>

<template>
  <div class="ingest">
    <div class="ingest__head">
      <div>
        <h2 class="ingest__title">数据接入</h2>
        <div class="ingest__sub">
          上传平台导出的 xlsx → 解析 → 落库。
          <b>≤ 20MB</b>，<b>平台必须和文件对得上</b> ✅
        </div>
      </div>
    </div>

    <!-- tab 切换（xlsx / 图片包）✅ -->
    <el-radio-group v-model="mode" class="ingest__mode">
      <el-radio-button value="xlsx">数据表（xlsx）</el-radio-button>
      <el-radio-button value="images">图片包（zip）</el-radio-button>
    </el-radio-group>

    <ImageIngest v-if="mode === 'images'" />

    <template v-else>
    <!-- ═══════ 上传区 ═══════ -->
    <ElCard shadow="never">
      <div class="ingest__form">
        <div class="ingest__field">
          <div class="ingest__field-label">平台</div>
          <ElSelect v-model="platform" class="ingest__sel">
            <ElOption value="taobao" label="淘宝" />
            <ElOption value="jd" label="京东" />
          </ElSelect>
        </div>

        <div class="ingest__field">
          <div class="ingest__field-label">数据类型</div>
          <ElSelect v-model="dataType" class="ingest__sel">
            <ElOption v-for="t in TYPES" :key="t.value" :value="t.value" :label="t.label" />
          </ElSelect>
        </div>

        <div class="ingest__field ingest__field--grow">
          <div class="ingest__field-label">&nbsp;</div>
          <ElUpload :auto-upload="false" :show-file-list="false"
                    :before-upload="beforeUpload"
                    :on-change="(f: { raw?: UploadRawFile }) => f.raw && doUpload(f.raw)"
                    accept=".xlsx,.xlsm" :disabled="uploading" drag
                    class="ingest__drop">
            <div class="ingest__drop-inner">
              <el-icon :size="26" color="#409eff"><UploadFilled /></el-icon>
              <div class="ingest__drop-text">
                {{ uploading ? `上传中 ${pct}%…` : '把 xlsx 拖到这里，或点击选择文件' }}
              </div>
            </div>
          </ElUpload>
        </div>
      </div>

      <div class="ingest__hint">
        当前选择：<b>{{ platform === 'jd' ? '京东' : '淘宝' }}</b> ·
        <b>{{ TYPES.find((t) => t.value === dataType)?.label }}</b>
        —— {{ TYPES.find((t) => t.value === dataType)?.hint }}
      </div>

      <ElAlert v-if="errorMsg" :title="errorMsg" type="error" show-icon :closable="false"
               class="ingest__alert" />
    </ElCard>

    <!-- ═══════ 本次结果 ═══════ -->
    <ElCard v-if="result" shadow="never" class="ingest__result">
      <template #header>
        <div class="ingest__result-head">
          <span class="ingest__card-title">本次结果</span>
          <ElTag :type="result.existed ? 'info' : (result.rows_ok > 0 ? 'success' : 'warning')"
                 effect="dark" size="small">
            {{ result.existed ? '已导过（没重复写）' : (result.rows_ok > 0 ? '成功' : '一行都没入库') }}
          </ElTag>
        </div>
      </template>

      <div class="ingest__kv">
        <div class="ingest__kv-item"><span>文件</span><b>{{ result.file }}</b></div>
        <div class="ingest__kv-item"><span>平台/类型</span><b>{{ result.platform }} / {{ result.data_type }}</b></div>
        <div class="ingest__kv-item"><span>大小</span><b>{{ (result.bytes / 1024).toFixed(0) }} KB</b></div>
        <div class="ingest__kv-item"><span>成功行</span><b class="is-ok">{{ result.rows_ok }}</b></div>
        <div class="ingest__kv-item"><span>坏行</span><b :class="result.rows_bad ? 'is-bad' : ''">{{ result.rows_bad }}</b></div>
        <div class="ingest__kv-item"><span>batch</span><b><code>{{ result.batch_id.slice(0, 8) }}</code></b></div>
      </div>

      <!-- 🚨 平台传错时，这里就是"为什么 0 条" -->
      <div v-if="result.warnings?.length" class="ingest__warn">
        <div v-for="(w, i) in result.warnings" :key="i" class="ingest__warn-line">
            <span class="ingest__warn-msg">{{ msgOf(w) }}</span>
            <span v-if="hintOf(w)" class="ingest__warn-hint">→ {{ hintOf(w) }}</span>
          </div>
      </div>

      <div class="ingest__next">
        ⚠️ 上传后**不会自动重跑全链** —— 新数据要生效，去
        <RouterLink to="/ops">运维台</RouterLink> 点「触发全链」（约 710 秒）✅
        <br />
        ⚠️ **销售数据导进来立刻就能用**（GMV TopN / 价格散点会变 ✅，不用跑全链）
      </div>
    </ElCard>

    <!-- ═══════ 库里现在有多少 ═══════ -->
    <ElCard shadow="never">
      <template #header><span class="ingest__card-title">库里现在有多少</span></template>
      <div v-if="!dataCounts.length" class="ingest__muted">
        （没读到 /api/status，不影响上传 ✅）
      </div>
      <div v-else class="ingest__counts">
        <div v-for="c in dataCounts" :key="c.label" class="ingest__count">
          <div class="ingest__count-value">{{ c.value }}</div>
          <div class="ingest__count-label">{{ c.label }}</div>
        </div>
      </div>
    </ElCard>

    <!-- ═══════ 本次会话上传过的 ═══════ -->
    <ElCard v-if="history.length > 1" shadow="never">
      <template #header><span class="ingest__card-title">本次会话上传过（{{ history.length }} 个）</span></template>
      <ElTable :data="history" size="small" stripe>
        <ElTableColumn prop="file" label="文件" min-width="220" show-overflow-tooltip />
        <ElTableColumn prop="platform" label="平台" width="80" />
        <ElTableColumn prop="data_type" label="类型" width="80" />
        <ElTableColumn label="入库" width="90" align="right" class-name="is-numeric">
          <template #default="{ row }">
            <span :class="row.rows_ok ? 'is-ok' : 'is-bad'">{{ row.rows_ok }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="坏行" width="90" align="right" class-name="is-numeric">
          <template #default="{ row }">{{ row.rows_bad }}</template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="110">
          <template #default="{ row }">
            <ElTag size="small" :type="row.existed ? 'info' : (row.rows_ok ? 'success' : 'warning')">
              {{ row.existed ? '已导过' : (row.rows_ok ? '成功' : '全坏') }}
            </ElTag>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
    </template>
  </div>
</template>

<style scoped lang="scss">
.ingest {
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

  &__mode {
    margin-bottom: -4px;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__form {
    display: flex;
    gap: $space-md;
    align-items: flex-start;
    flex-wrap: wrap;
  }

  &__field {
    display: flex;
    flex-direction: column;
    gap: 4px;

    &--grow {
      flex: 1 1 320px;
      min-width: 280px;
    }
  }

  &__field-label {
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__sel {
    width: 140px;
  }

  &__drop {
    width: 100%;

    :deep(.el-upload),
    :deep(.el-upload-dragger) {
      width: 100%;
    }

    :deep(.el-upload-dragger) {
      padding: 14px;
    }
  }

  &__drop-inner {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: $space-sm;
  }

  &__drop-text {
    font-size: 13px;
    color: var(--hub-text-regular);
  }

  &__hint {
    margin-top: $space-sm;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__alert {
    margin-top: $space-sm;
  }

  &__card-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__result-head {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__kv {
    display: flex;
    flex-wrap: wrap;
    gap: $space-lg;
  }

  &__kv-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 12px;

    span {
      font-size: 11px;
      color: var(--hub-text-secondary);
    }

    b {
      font-weight: 600;

      &.is-ok { color: var(--el-color-success); }
      &.is-bad { color: var(--el-color-danger); }
    }

    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
    }
  }

  &__warn {
    margin-top: $space-md;
    padding: 10px 12px;
    border-left: 3px solid var(--el-color-warning);
    border-radius: 0 $radius-sm $radius-sm 0;
    background: var(--el-color-warning-light-9);
  }

  &__warn-msg {
    font-weight: 500;
  }

  &__warn-hint {
    margin-left: 4px;
    color: var(--el-color-primary);
  }

  &__warn-line {
    font-size: 12px;
    line-height: 1.8;
    color: var(--el-color-warning-dark-2);
  }

  &__next {
    margin-top: $space-md;
    font-size: 11px;
    line-height: 1.8;
    color: var(--hub-text-secondary);
  }

  &__counts {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: $space-md;
  }

  &__count {
    padding: 10px 12px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);
  }

  &__count-value {
    font-size: 20px;
    font-weight: 600;
    @include tabular-nums;
  }

  &__count-label {
    margin-top: 2px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__muted {
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  :deep(.is-ok) { color: var(--el-color-success); }
  :deep(.is-bad) { color: var(--el-color-danger); }
}
</style>
