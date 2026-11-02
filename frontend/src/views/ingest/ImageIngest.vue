<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  图片包上传（M2 多模态）—— 「数据接入」页的第二个 tab
//  ⚠️ **语义和 xlsx 不同**：
//     xlsx   = **新数据进来**（评价/商品/销售）
//     图片包 = **给已有评价补打标**（M2 是"处理待打标的评价"，不新增评价 ✅）
//  ⚠️ **合规红线**：图片**永不入库、永不出网**；
//     沙箱里处理完**覆写销毁**，`residual` 必须是 0 ✅
// ══════════════════════════════════════════════════════════════
import { computed, ref } from 'vue'
import {
  ElAlert, ElCard, ElMessage, ElOption, ElSelect, ElSwitch, ElTag, ElUpload,
} from 'element-plus'
import type { UploadRawFile } from 'element-plus'
import { PictureFilled } from '@element-plus/icons-vue'

import { ingestImages, type ImageIngestResult } from '@/api/write'
import { ApiError } from '@/api/client'

const vision = ref<'real' | 'mock'>('real')
const force = ref(true)
const uploading = ref(false)
const pct = ref(0)
const result = ref<ImageIngestResult | null>(null)
const errorMsg = ref('')

/** zip 上限 1GB（后端限制 ✅，真实 images.zip 471MB）*/
const MAX = 1024 * 1024 * 1024

function beforeUpload(file: UploadRawFile): boolean {
  if (!/\.zip$/i.test(file.name)) {
    ElMessage.error('图片包只接受 .zip（把图片打包成 zip 再传 ✅）')
    return false
  }
  if (file.size > MAX) {
    ElMessage.error(`文件 ${(file.size / 1024 / 1024).toFixed(0)}MB，超过 1GB 上限 ✅`)
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
    const r = await ingestImages(file, vision.value, force.value, (p) => { pct.value = p })
    result.value = r
    if (r.residual !== 0) {
      ElMessage.error(`⚠️ 沙箱残留 ${r.residual} 个文件 —— 这是 bug ✅`)
    } else if (r.processed === 0) {
      ElMessage.warning('处理了 0 条 —— 可能是"图不在包里"或"没有待打标的评价" ✅')
    } else {
      ElMessage.success(`处理完成：${r.processed} 条评价，销毁 ${r.destroyed} 张图 ✅`)
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

const visionHint = computed(() =>
  vision.value === 'real'
    ? '调本地 qwen2.5vl:3b —— **428 张图要几分钟**，但标签是真的 ✅'
    : '**假标签**，只用来验证连通性；别拿它的结果当分析结论 ✅',
)
</script>

<template>
  <div class="img-ingest">
    <!-- 语义说明：这一条最容易被误解 -->
    <ElAlert type="info" :closable="false" show-icon class="img-ingest__note">
      <template #title>图片包是给<b>已有评价补打标</b>，不会新增评价 ✅</template>
      <div class="img-ingest__note-body">
        流程：解 zip → 沙箱 → pHash 去重 → Vision 打标 → 元数据剥离（GPS/设备信息）→
        <b>覆写销毁原图</b>。<br />
        图片<b>永不入库、永不出网</b>（<code>vision.py</code> 硬编码只连本地 ✅）。
      </div>
    </ElAlert>

    <ElCard shadow="never">
      <div class="img-ingest__form">
        <div class="img-ingest__field">
          <div class="img-ingest__label">视觉模型</div>
          <ElSelect v-model="vision" class="img-ingest__sel">
            <ElOption value="real" label="real（本地真模型）" />
            <ElOption value="mock" label="mock（假标签，仅测试）" />
          </ElSelect>
        </div>

        <div class="img-ingest__field">
          <div class="img-ingest__label">重跑已处理的</div>
          <ElSwitch v-model="force" />
        </div>

        <div class="img-ingest__field img-ingest__field--grow">
          <div class="img-ingest__label">&nbsp;</div>
          <ElUpload :auto-upload="false" :show-file-list="false"
                    :before-upload="beforeUpload"
                    :on-change="(f: { raw?: UploadRawFile }) => f.raw && doUpload(f.raw)"
                    accept=".zip" :disabled="uploading" drag class="img-ingest__drop">
            <div class="img-ingest__drop-inner">
              <el-icon :size="26" color="#409eff"><PictureFilled /></el-icon>
              <div class="img-ingest__drop-text">
                {{ uploading ? `上传中 ${pct}%…` : '把图片包 zip 拖到这里（≤1GB）' }}
              </div>
            </div>
          </ElUpload>
        </div>
      </div>

      <div class="img-ingest__hint">{{ visionHint }}</div>

      <div v-if="!force" class="img-ingest__hint is-warn">
        ⚠️ 关掉「重跑已处理的」→ 只会处理**还没打标**的评价；
        库里如果都已打过标，会返回 0 条 ✅
      </div>

      <ElAlert v-if="errorMsg" :title="errorMsg" type="error" show-icon
               :closable="false" class="img-ingest__alert" />
    </ElCard>

    <!-- 结果 -->
    <ElCard v-if="result" shadow="never" class="img-ingest__result">
      <template #header>
        <div class="img-ingest__result-head">
          <span class="img-ingest__card-title">处理结果</span>
          <ElTag :type="result.residual === 0 ? 'success' : 'danger'" effect="dark" size="small">
            {{ result.residual === 0 ? '沙箱零残留 ✅' : `残留 ${result.residual} ⚠️` }}
          </ElTag>
          <span class="img-ingest__muted">vision = {{ result.vision }} · {{ result.elapsed_s }}s</span>
        </div>
      </template>

      <div class="img-ingest__kv">
        <div class="img-ingest__kv-item">
          <span>包内图片</span><b>{{ result.images }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>pHash 去重</span><b>{{ result.deduped }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>成功处理评价</span><b class="is-ok">{{ result.processed }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>图不在包内（跳过）</span><b>{{ result.skipped }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>失败</span><b :class="result.failed ? 'is-bad' : ''">{{ result.failed }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>元数据剥离</span><b>{{ result.stripped }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>覆写销毁</span><b>{{ result.destroyed }}</b>
        </div>
        <div class="img-ingest__kv-item">
          <span>trace_id</span><b><code>{{ result.trace_id || '—' }}</code></b>
        </div>
      </div>

      <div class="img-ingest__next">
        ✅ 打标结果写进了 <code>review_feature</code>；
        它会影响 M3 的分类和 agent 的证据（图里有没有破洞/色差 ✅）。<br />
        ⚠️ 「图不在包内（跳过）」不是错误 —— 说明这个 zip 只覆盖了**部分**评价的图片 ✅
      </div>
    </ElCard>
  </div>
</template>

<style scoped lang="scss">
.img-ingest {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__note-body {
    margin-top: 4px;
    font-size: 12px;
    line-height: 1.8;

    code {
      padding: 1px 4px;
      border-radius: 3px;
      background: var(--el-fill-color-light);
    }
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

  &__label {
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__sel {
    width: 190px;
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
    line-height: 1.7;
    color: var(--hub-text-secondary);

    &.is-warn {
      color: var(--el-color-warning-dark-2);
    }
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

  &__muted {
    font-size: 11px;
    color: var(--hub-text-secondary);
    font-weight: 400;
  }

  &__kv {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: $space-md;
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
      @include tabular-nums;

      &.is-ok { color: var(--el-color-success); }
      &.is-bad { color: var(--el-color-danger); }
    }

    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
    }
  }

  &__next {
    margin-top: $space-md;
    font-size: 11px;
    line-height: 1.8;
    color: var(--hub-text-secondary);

    code {
      padding: 1px 3px;
      border-radius: 3px;
      background: var(--el-fill-color-light);
    }
  }
}
</style>
