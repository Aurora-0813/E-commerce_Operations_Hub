<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  全局分析进度（AnalysisBar.vue）—— 2026-11-03 新增
// ══════════════════════════════════════════════════════════════
// **解决的问题**（用户反馈）
//   "链的运行状态不透明，改成流程图示的样子，可用 element 步骤条" ✅
//   "并且可以后台运行，不影响其他页面的点击" ✅
//
// **放在哪**：导航栏里一个小胶囊（不占地方）
//   · 跑着的时候：**一条细进度条 + 当前阶段名**（任何页面都看得到 ✅）
//   · 点一下 → Popover 展开 **Element Steps**（9 个阶段 + 每个阶段在干什么 ✅）
//   · 跑完 → 胶囊变绿色「分析完成」+ 点一下跳工作台
//   · 没跑过 → 什么都不显示（不打扰 ✅）
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElIcon, ElPopover, ElSteps, ElStep, ElTag } from 'element-plus'
import { Loading, CircleCheck, Warning } from '@element-plus/icons-vue'

import { STAGES, useAnalysisStore } from '@/stores/analysis'

const analysis = useAnalysisStore()
const router = useRouter()

onMounted(() => {
  // **刷新/切页回来自动接上**（run_id 在 localStorage ✅）
  analysis.restore()
})

const STATUS_TEXT: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '分析中',
  DONE: '分析完成',
  DEGRADED: '完成（降级）',
  FAILED: '分析失败',
  DEAD: '分析失败',
}

function tone(): 'primary' | 'success' | 'danger' | 'warning' {
  if (analysis.finished) return analysis.degraded ? 'warning' : 'success'
  if (analysis.failed) return 'danger'
  return 'primary'
}

function gotoResult(): void {
  // 跑完 → 去工作台看结论；失败 → 去运维台看日志 ✅
  void router.push(analysis.failed ? '/ops' : '/dashboard')
}
</script>

<template>
  <!-- 没跑过 → 不显示（别打扰）-->
  <div v-if="analysis.runId && analysis.status" class="abar">
    <ElPopover placement="bottom-end" :width="440" trigger="click">
      <template #reference>
        <button class="abar__pill" :class="`is-${tone}`" type="button">
          <!-- 细进度条（贴胶囊底部）-->
          <span class="abar__fill" :style="{ width: `${analysis.percent}%` }" />

          <ElIcon v-if="analysis.running" class="is-spin" :size="13">
            <Loading />
          </ElIcon>
          <ElIcon v-else-if="analysis.finished && !analysis.degraded" :size="13">
            <CircleCheck />
          </ElIcon>
          <ElIcon v-else :size="13">
            <Warning />
          </ElIcon>

          <span class="abar__label">
            {{ STATUS_TEXT[analysis.status] ?? analysis.status }}
            <template v-if="analysis.running && analysis.currentStage">
              · {{ STAGES.find((s) => s.key === analysis.currentStage)?.title ?? analysis.currentStage }}
            </template>
          </span>
          <span v-if="analysis.running" class="abar__pct">{{ analysis.percent }}%</span>
        </button>
      </template>

      <!-- 展开：9 个阶段的步骤条 -->
      <div class="abar__pop">
        <div class="abar__head">
          <span class="abar__title">分析进度（9 个阶段）</span>
          <ElTag :type="tone()" size="small" effect="dark">
            {{ STATUS_TEXT[analysis.status] ?? analysis.status }}
          </ElTag>
        </div>

        <ElSteps :active="analysis.stepIndex" direction="vertical" size="small"
                 class="abar__steps">
          <ElStep v-for="s in STAGES" :key="s.key" :title="s.title"
                  :description="s.what" :status="analysis.stageStatus(s.key)" />
        </ElSteps>

        <div v-if="analysis.errorMsg" class="abar__err">
          ❌ {{ analysis.errorMsg }}
        </div>

        <div class="abar__foot">
          <span class="abar__hint">
            <template v-if="analysis.running">
              它在后台跑，你可以去做别的事 切页面不会中断
            </template>
            <template v-else-if="analysis.finished">
              {{ analysis.percent }}% 完成 —— 去工作台看「今天该做什么」
            </template>
            <template v-else>
              分析失败了 —— 去运维台看日志
            </template>
          </span>
          <ElButton v-if="!analysis.running" size="small" :type="tone()" plain
                    @click="gotoResult">
            去看结果
          </ElButton>
        </div>
      </div>
    </ElPopover>
  </div>
</template>

<style scoped lang="scss">
.abar {
  position: relative;

  &__pill {
    position: relative;
    display: inline-flex;
    flex: 0 1 auto;        /* **可收缩**：空间不够时自己缩，不挤别人 ✅ */
    min-width: 0;
    align-items: center;
    gap: 6px;
    max-width: 190px;
    height: 28px;
    padding: 0 10px;
    overflow: hidden;
    font-size: 12px;
    color: var(--hub-text-primary);
    cursor: pointer;
    background: var(--hub-bg-card);
    border: 1px solid var(--hub-border-light);
    border-radius: 14px;
    transition: border-color $duration-fast;

    &:hover {
      border-color: var(--el-color-primary-light-5);
    }

    &.is-primary {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary-light-7);
    }

    &.is-success {
      color: var(--el-color-success);
      border-color: var(--el-color-success-light-7);
    }

    &.is-danger {
      color: var(--el-color-danger);
      border-color: var(--el-color-danger-light-7);
    }

    &.is-warning {
      color: var(--el-color-warning);
      border-color: var(--el-color-warning-light-7);
    }
  }

  &__pill.is-running {
    color: var(--el-color-primary);
    border-color: var(--el-color-primary-light-5);
    background: var(--el-color-primary-light-9);
    animation: abar-breathe 1.6s ease-in-out infinite;
  }

  &__pill.is-success {
    color: var(--el-color-white, #fff);
    background: var(--el-color-success);
    border-color: var(--el-color-success);
  }

  /* 贴底部的细进度条（像 YouTube ✅）*/
  &__fill {
    position: absolute;
    left: 0;
    bottom: 0;
    height: 2px;
    background: currentcolor;
    transition: width $duration-base ease;
  }

  &__label {
    position: relative;
    overflow: hidden;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__pct {
    position: relative;
    font-variant-numeric: tabular-nums;
    opacity: 0.75;
  }

  &__pop {
    display: flex;
    flex-direction: column;
    gap: $space-sm;
  }

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
  }

  &__steps {
    max-height: 320px;
    padding-left: 2px;
    overflow-y: auto;
  }

  &__err {
    padding: 6px 8px;
    font-size: 12px;
    color: var(--el-color-danger);
    background: var(--el-color-danger-light-9);
    border-radius: $radius-sm;
  }

  &__foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-sm;
    padding-top: $space-sm;
    border-top: 1px dashed var(--hub-border-light);
  }

  &__hint {
    font-size: 11px;
    line-height: 1.6;
    color: var(--hub-text-secondary);
  }
}

.is-spin {
  animation: abar-spin 1s linear infinite;
}

@keyframes abar-breathe {
  0%, 100% {
    opacity: 1;
  }

  50% {
    opacity: 0.72;
  }
}

@keyframes abar-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-spin,
  .abar__pill.is-running {
    animation: none;
  }
}
</style>
