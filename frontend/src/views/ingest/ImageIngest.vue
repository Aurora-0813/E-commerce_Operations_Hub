<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  图片包上传（M2 多模态）—— 「数据接入」页的第二个 tab
//  ⚠️ **语义和 xlsx 不同**：
//     xlsx   = **新数据进来**（评价/商品/销售）
//     图片包 = **给已有评价补打标**（M2 是"处理待打标的评价"，不新增评价 ✅）
//  ⚠️ **合规红线**：图片**永不入库、永不出网**；
//     沙箱里处理完**覆写销毁**，`residual` 必须是 0 ✅
// ══════════════════════════════════════════════════════════════
import { ref } from 'vue'
import {
  ElAlert, ElCard, ElMessage, ElSwitch, ElTag, ElUpload,
} from 'element-plus'
import { PictureFilled } from '@element-plus/icons-vue'

import {
  ingestImages, precheckImages,
  type ImageIngestResult, type PrecheckResp,
} from '@/api/write'
import { ApiError } from '@/api/client'

// **只保留真实的那个**（用户要求：视觉模型选择删掉，只剩 1 个真实的 ✅）
const vision = ref<'real' | 'mock'>('real')
const force = ref(true)

// ══════════════════════════════════════════════════════════════
//  预检（2026-11-03 加）—— **先对账，再决定跑不跑**
// ══════════════════════════════════════════════════════════════
// **为什么必须这一步**
//   真包 428 张 x 8.9 秒 约 63 分钟；先跑再发现只匹配上 1 条 = 白烧一小时
//   → 预检不解压、不调模型、秒回，先让用户看清对账
const prechecking = ref(false)
const pre = ref<PrecheckResp | null>(null)
const pendingFile = ref<File | null>(null)

async function doPrecheck(file: File): Promise<void> {
  prechecking.value = true
  pre.value = null
  pendingFile.value = file
  try {
    pre.value = await precheckImages(file)
  // ⚠️ **对账完直接入队**（2026-11-04 改）
  //    用户："上传成功后不是开始分析的第 2 步才看图片吗？下面为什么会有个确认处理？"
  //    → 去掉确认那一步：**对账 → 直接入队** ✅
  //    （对账仍然有价值：它会**先告你能匹配多少**，匹配不上就不白跑 ✅）
  if (pre.value && (pre.value.reviews_matched ?? 0) > 0) {
    await doUpload(file)
  } else if (pre.value) {
    ElMessage.warning('这个包里没有一条能对上现有评价的图片 —— 先别跑，看看对账里的样例 ✅')
  }
  } catch {
    pre.value = null
  } finally {
    prechecking.value = false
  }
}
const uploading = ref(false)
const pct = ref(0)
const result = ref<ImageIngestResult | null>(null)
const errorMsg = ref('')

async function doUpload(file: File): Promise<void> {
  uploading.value = true
  pct.value = 0
  result.value = null
  errorMsg.value = ''
  try {
    ElMessage.info('已入队，正在后台处理 —— 可以切走，回来还能看到进度')
    const r = await ingestImages(file, vision.value, force.value, (p) => { pct.value = p })
    result.value = r
    ElMessage.success(`处理完成：${r.images} 张图 / ${r.processed} 条评价`)
    if (r.residual !== 0) {
      ElMessage.error(`⚠️ 沙箱残留 ${r.residual} 个文件 —— 这是 bug`)
    } else if (r.processed === 0) {
      ElMessage.warning('处理了 0 条 —— 可能是"图不在包里"或"没有待打标的评价"')
    } else {
      ElMessage.success(`处理完成：${r.processed} 条评价，销毁 ${r.destroyed} 张图`)
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

</script>

<template>
  <div class="img-ingest">
    <!-- 语义说明：这一条最容易被误解 -->
    <ElAlert type="info" :closable="false" show-icon class="img-ingest__note">
      <template #title>图片包是给<b>已有评价补打标</b>，不会新增评价</template>
      <div class="img-ingest__note-body">
        流程：解 zip → 沙箱 → pHash 去重 → Vision 打标 → 元数据剥离（GPS/设备信息）→
        <b>覆写销毁原图</b>。<br />
        图片<b>永不入库、永不出网</b>（<code>vision.py</code> 硬编码只连本地）。
      </div>
    </ElAlert>

    <ElCard shadow="never">
      <div class="img-ingest__form">

        <div class="img-ingest__field">
          <div class="img-ingest__label">重跑已处理的</div>
          <ElSwitch v-model="force" />
        </div>

        <div class="img-ingest__field img-ingest__field--grow">
          <div class="img-ingest__label">&nbsp;</div>
          <!-- ⚠️ 先预检，再处理（2026-11-03 加）
               真包几百张图，先跑再发现"只匹配上 1 条"= 白等
               → 拖进来先调 `/api/ingest/images/precheck`（不解压、不调模型、秒回）
               → 用户看完对账，点「确认处理」才真的跑 -->
          <ElUpload :auto-upload="false" :show-file-list="false"
                    :on-change="(f: { raw?: File }) => f.raw && void doPrecheck(f.raw)"
                    :before-upload="() => false"
                    accept=".zip" :disabled="uploading" drag class="img-ingest__drop">
            <div class="img-ingest__drop-inner">
              <el-icon :size="26" color="#409eff"><PictureFilled /></el-icon>
              <div class="img-ingest__drop-text">
                {{ uploading ? `上传中 ${pct}%…` : '把图片包 zip 拖到这里（先对账，不立刻跑）' }}
              </div>
            </div>
          </ElUpload>
        </div>
      </div>

      <!-- ══════════════════════════════════════════════════════
           预检对账结果（2026-11-03 加）
           ══════════════════════════════════════════════════════ -->
      <ElCard v-if="prechecking || pre" shadow="never" class="img-ingest__pre">
        <template #header>
          <div class="img-ingest__result-head">
            <span class="img-ingest__card-title">对账（还没跑模型）</span>
            <span class="img-ingest__muted">{{ pendingFile?.name }}</span>
          </div>
        </template>

        <div v-if="prechecking" class="img-ingest__muted">正在对账（不解压、不调模型，应该几秒）…</div>

        <template v-else-if="pre">
          <!-- 一句人话结论 -->
          <div class="img-ingest__verdict">{{ pre.verdict }}</div>

          <div class="img-ingest__pre-grid">
            <div><span class="img-ingest__muted">包里图片</span><b>{{ pre.package_files }}</b></div>
            <div><span class="img-ingest__muted">有图评价</span><b>{{ pre.reviews_with_image }}</b></div>
            <div><span class="img-ingest__muted">能对上</span><b class="is-ok">{{ pre.reviews_matched }}</b></div>
            <div><span class="img-ingest__muted">对不上</span><b :class="pre.reviews_unmatched ? 'is-bad' : ''">{{ pre.reviews_unmatched }}</b></div>
            <div><span class="img-ingest__muted">匹配到的图</span><b>{{ pre.images_matched }}</b></div>
            <div><span class="img-ingest__muted">包里多余</span><b>{{ pre.images_unused }}</b></div>
            <div><span class="img-ingest__muted">预计调模型</span><b>{{ pre.est_calls }} 次</b></div>
            <div><span class="img-ingest__muted">要调模型</span><b>{{ pre.est_calls }} 次</b></div>
          </div>

          <div class="img-ingest__muted">
            用的匹配规则：<b>{{ pre.tier_text || '（没匹配上）' }}</b>
            <template v-if="pre.manifest_source">｜manifest：<code>{{ pre.manifest_source }}</code>（{{ pre.manifest_rows }} 行）</template>
            <template v-if="pre.name_collisions">｜⚠️ 同名冲突 {{ pre.name_collisions }} 张（不同子目录同名 → 不能用于目录匹配）</template>
          </div>

          <!-- 对不上的样例（给证据，用户才能自己判断）-->
          <div v-if="pre.unmatched_sample.length" class="img-ingest__pre-sample">
            <div class="img-ingest__muted">对不上的样例（期望的文件名在包里找不到）：</div>
            <div v-for="s in pre.unmatched_sample.slice(0, 4)" :key="s.review_id" class="img-ingest__pre-row">
              <code>{{ s.review_id }}</code>
              <span class="img-ingest__muted">期望：{{ s.expected_files.join(' / ') || '—' }}</span>
            </div>
          </div>

          <!-- 缺失的样例（要 N 张只找到 M 张）-->
          <div v-if="pre.missing_sample.length" class="img-ingest__pre-sample">
            <div class="img-ingest__muted">部分缺失（评价要几张、只找到几张）：</div>
            <div v-for="s in pre.missing_sample.slice(0, 4)" :key="s.review_id" class="img-ingest__pre-row">
              <code>{{ s.review_id }}</code>
              <span class="img-ingest__muted">要 {{ s.expected }} 张，只找到 {{ s.found }} 张</span>
            </div>
          </div>

          <div class="img-ingest__pre-actions">
            <span v-if="pre.reviews_matched === 0" class="img-ingest__hint is-warn">
              一条都对不上 → 先按上面的提示改命名（或放一份 index.csv）
            </span>
          </div>
        </template>
      </ElCard>

      

      <div v-if="!force" class="img-ingest__hint is-warn">
        ⚠️ 关掉「重跑已处理的」→ 只会处理还没打标的评价；
        库里如果都已打过标，会返回 0 条
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
            {{ result.residual === 0 ? '沙箱零残留' : `残留 ${result.residual} ⚠️` }}
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
        打标结果写进了 <code>review_feature</code>；
        它会影响 M3 的分类和 agent 的证据（图里有没有破洞/色差）。<br />
        ⚠️ 「图不在包内（跳过）」不是错误 —— 说明这个 zip 只覆盖了部分评价的图片
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

  &__pre {
    margin-top: $space-md;
  }

  &__verdict {
    margin-bottom: $space-sm;
    padding: $space-sm $space-md;
    border-radius: $radius-base;
    font-size: 13px;
    line-height: 1.7;
    background: var(--el-color-primary-light-9);
    border: 1px solid var(--el-color-primary-light-7);
  }

  &__pre-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: $space-sm;
    margin-bottom: $space-sm;
    font-size: 12px;

    div {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 6px $space-sm;
      border-radius: $radius-sm;
      background: var(--hub-bg-hover, #f5f7fa);
    }

    b {
      font-size: 16px;
      font-variant-numeric: tabular-nums;
    }

    .is-ok {
      color: var(--el-color-success);
    }

    .is-bad {
      color: var(--el-color-danger);
    }

    @media (max-width: 1100px) {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  &__pre-sample {
    margin-top: $space-sm;
    font-size: 12px;
  }

  &__pre-row {
    display: flex;
    gap: $space-sm;
    padding: 2px 0;
  }

  &__pre-actions {
    display: flex;
    align-items: center;
    gap: $space-sm;
    margin-top: $space-md;
    padding-top: $space-sm;
    border-top: 1px dashed var(--hub-border-light);
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
