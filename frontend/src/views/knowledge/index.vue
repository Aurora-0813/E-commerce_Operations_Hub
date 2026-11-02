<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  8 号页：知识库（㉒）
//  数据源：GET /api/knowledge（含 **effective**）、POST /api/knowledge、POST /{id}/publish
//  ⚠️ **现在 0 条** → 空态要能引导"新建一条" ✅
//  ⚠️ `prompt_patch` **只能追加、不能覆盖**系统提示 ✅（后端保证，页面写清）
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref, watch } from 'vue'
import {
  ElButton, ElCard, ElDialog, ElForm, ElFormItem, ElInput, ElMessage,
  ElOption, ElSelect, ElTag,
} from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'

import http from '@/api/client'
import { createKnowledge, publishKnowledge } from '@/api/write'
import { ApiError } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import { useUserStore } from '@/stores/user'

interface KnowledgeRow {
  id: string
  type: string
  title: string
  content: string
  topic: string | null
  status: 'DRAFT' | 'PUBLISHED'
  version: number
  /** ✅ **现在是否在影响 AI**（只有 PUBLISHED 才注入提示词）*/
  effective: boolean
  effective_note: string
  created_at: string | null
}

const user = useUserStore()
const canPublish = computed(() => user.hasRole('supervisor'))

const loading = ref(true)
const error = ref<unknown>(null)
const rows = ref<KnowledgeRow[]>([])
const total = ref(0)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const { data } = await http.get<{ total: number; items: KnowledgeRow[] }>(
      '/api/knowledge', { params: { limit: 100 } })
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(() => void load())

// ---------- 新建 ----------
const dialog = ref(false)
const saving = ref(false)
const form = ref({ title: '', content: '', topic: '' })

const TOPICS = [
  { value: '', label: '全局（对所有问题生效）' },
  { value: 'quality', label: '质量' },
  { value: 'size', label: '尺码' },
  { value: 'logistics', label: '物流' },
  { value: 'service', label: '服务' },
  { value: 'color', label: '色差' },
  { value: 'price', label: '价格' },
]

function openCreate(): void {
  form.value = { title: '', content: '', topic: '' }
  dialog.value = true
}

async function doCreate(): Promise<void> {
  if (!form.value.title.trim() || !form.value.content.trim()) return
  saving.value = true
  try {
    await createKnowledge({
      title: form.value.title.trim(),
      content: form.value.content.trim(),
      topic: form.value.topic || null,
    })
    ElMessage.success('已创建（**DRAFT 不生效**，要发布才注入提示词 ✅）')
    dialog.value = false
    await load()
  } finally {
    saving.value = false
  }
}

// ---------- 发布 ----------
const publishingId = ref('')
async function doPublish(row: KnowledgeRow): Promise<void> {
  publishingId.value = row.id
  try {
    const r = await publishKnowledge(row.id)
    ElMessage.success(`已发布 v${r.version} —— **立刻注入提示词** ✅`)
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      ElMessage.info('这条已经发布过了 ✅')
    }
    // 403 已由拦截器提示
  } finally {
    publishingId.value = ''
  }
}

// ---------- 展示 ----------
const TYPE_LABEL: Record<string, string> = {
  prompt_patch: '口径补丁（prompt_patch）',
  playbook: '处置手册',
  faq: '常见问答',
}
const TOPIC_LABEL: Record<string, string> = {
  quality: '质量', size: '尺码', logistics: '物流', service: '服务',
  color: '色差', price: '价格',
}
watch(dialog, (v) => { if (!v) saving.value = false })
</script>

<template>
  <div class="kb">
    <div class="kb__head">
      <div>
        <h2 class="kb__title">知识库</h2>
        <div class="kb__sub">
          业务方发一条口径 → AI 的行为就变，<b>不用改代码</b> ✅
          <span class="kb__note">
            （`prompt_patch` 只能<b>追加</b>，不能覆盖系统提示 —— 否则能拆掉反幻觉防线 ✅）
          </span>
        </div>
      </div>
      <div class="kb__actions">
        <ElButton :icon="Refresh" :loading="loading" @click="load()">刷新</ElButton>
        <ElButton type="primary" :icon="Plus" @click="openCreate()">新建条目</ElButton>
      </div>
    </div>

    <ErrorState v-if="error" :error="error" @retry="load()" />

    <ElCard v-else shadow="never">
      <EmptyState
        v-if="!loading && !rows.length"
        description="还没有知识条目"
        hint="新建一条试试：比如「质量问题先道歉，再解释原因，最后给补救方案」—— 发布后 AI 的回复就会按这个口径 ✅"
        command="POST /api/knowledge"
      />

      <div v-else v-loading="loading" class="kb__list">
        <div v-for="k in rows" :key="k.id" class="kb__item">
          <div class="kb__item-head">
            <span class="kb__item-title">{{ k.title }}</span>
            <ElTag size="small" :type="k.status === 'PUBLISHED' ? 'success' : 'info'">
              {{ k.status }} v{{ k.version }}
            </ElTag>
            <!-- ✅ <b>这一条现在是否在影响 AI</b> -->
            <ElTag size="small" :type="k.effective ? 'success' : 'warning'" effect="plain">
              {{ k.effective_note }}
            </ElTag>
            <span class="kb__grow" />
            <ElButton
              v-if="k.status === 'DRAFT'"
              size="small" type="primary"
              :loading="publishingId === k.id"
              :disabled="!canPublish"
              :title="canPublish ? '' : '需要 supervisor 角色'"
              @click="doPublish(k)"
            >
              发布
            </ElButton>
            <span v-else class="kb__live">已生效 ✅</span>
          </div>

          <div class="kb__item-meta">
            <span>{{ TYPE_LABEL[k.type] ?? k.type }}</span>
            <span>· {{ k.topic ? (TOPIC_LABEL[k.topic] ?? k.topic) : '全局' }}</span>
            <span v-if="k.created_at">
              · {{ k.created_at.slice(0, 16).replace('T', ' ') }}
            </span>
          </div>

          <div class="kb__item-content">{{ k.content }}</div>
        </div>
      </div>

      <div v-if="total" class="kb__count">共 {{ total }} 条</div>
    </ElCard>

    <!-- 新建弹窗 -->
    <ElDialog v-model="dialog" title="新建知识条目（DRAFT）" width="560px">
      <ElForm label-position="top">
        <ElFormItem label="标题（AI 也会看到，写清楚一句）" required>
          <ElInput v-model="form.title" maxlength="200" show-word-limit
                   placeholder="例如：质量问题回复口径" />
        </ElFormItem>
        <ElFormItem label="生效范围">
          <ElSelect v-model="form.topic" class="kb__dialog-select">
            <ElOption v-for="t in TOPICS" :key="t.value" :value="t.value" :label="t.label" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="内容（会<b>追加</b>到提示词，不覆盖系统规则）" required>
          <ElInput v-model="form.content" type="textarea" :rows="6" maxlength="4000"
                   show-word-limit
                   placeholder="例如：先道歉，再解释掉色原因（面料固色工艺），最后给出补救方案（换货/补偿券）" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialog = false">取消</ElButton>
        <ElButton type="primary" :loading="saving"
                  :disabled="!form.title.trim() || !form.content.trim()"
                  @click="doCreate()">
          创建（DRAFT，还不生效）
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped lang="scss">
.kb {
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
  }

  &__note {
    opacity: 0.8;
  }

  &__actions {
    display: flex;
    gap: $space-sm;
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: $space-sm;
  }

  &__item {
    padding: 12px 14px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);
  }

  &__item-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  &__item-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__grow {
    flex: 1 1 auto;
  }

  &__live {
    font-size: 11px;
    color: var(--el-color-success);
  }

  &__item-meta {
    display: flex;
    gap: 6px;
    margin-top: 4px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__item-content {
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.75;
    color: var(--hub-text-regular);
    white-space: pre-wrap;
  }

  &__count {
    margin-top: $space-sm;
    text-align: right;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__dialog-select {
    width: 100%;
  }
}
</style>
