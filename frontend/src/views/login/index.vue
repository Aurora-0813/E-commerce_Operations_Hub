<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  0 号页：登录
//  ⚠️ 登录成功后**必须**：写 token → 跳 `redirect` 或工作台
//  ⚠️ 失败统一话术（**后端防账号枚举** ✅，前端不要提示"用户不存在"）
// ══════════════════════════════════════════════════════════════
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElForm, ElFormItem, ElInput, ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { ApiError } from '@/api/client'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const user = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ email: '', password: '' })

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不对', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/** 演示账号（**只在前端提示，不做任何自动填充** ✅）*/
const DEMO = [
  { email: 'operator@hub.local', role: '运营' },
  { email: 'supervisor@hub.local', role: '主管（能批准）' },
]

function fill(email: string): void {
  form.email = email
  form.password = '123456'
}

async function submit(): Promise<void> {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return

  loading.value = true
  try {
    await user.login(form.email.trim(), form.password)
    ElMessage.success(`欢迎回来，${user.displayName || user.role}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } catch (e) {
    // 后端 401 的统一话术就是"邮箱或密码不对" ✅ —— 直接透出，**不加工**
    const msg = e instanceof ApiError ? e.message : (e instanceof Error ? e.message : '登录失败，请重试')
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <div class="login__panel">
      <!-- 左：品牌区 -->
      <div class="login__brand">
        <div class="login__logo">电</div>
        <h1 class="login__title">电商运营决策中枢</h1>
        <p class="login__sub">
          跨平台（淘宝 / 京东）评价 → 降噪 → 分类 → 问题簇 → 提案 → 审批 → 复盘
        </p>
        <ul class="login__points">
          <li>每条建议都能展开到<strong>原始评价</strong></li>
          <li>动作执行后<strong>代码回查</strong>，不信工具返回值</li>
          <li>效果用 <strong>DID + 置信区间</strong> 判，不靠感觉</li>
        </ul>
      </div>

      <!-- 右：表单区 -->
      <div class="login__form">
        <h2 class="login__form-title">登录</h2>
        <ElForm ref="formRef" :model="form" :rules="rules" label-position="top"
                size="large" @keyup.enter="submit">
          <ElFormItem label="邮箱" prop="email">
            <ElInput v-model="form.email" placeholder="operator@hub.local"
                     autocomplete="username" clearable />
          </ElFormItem>
          <ElFormItem label="密码" prop="password">
            <ElInput v-model="form.password" type="password" show-password
                     placeholder="••••••" autocomplete="current-password" />
          </ElFormItem>
          <ElButton type="primary" class="login__submit" :loading="loading"
                    size="large" @click="submit">
            登录
          </ElButton>
        </ElForm>

        <div class="login__demo">
          <div class="login__demo-title">本地演示账号（点一下就填好）</div>
          <div class="login__demo-list">
            <button v-for="d in DEMO" :key="d.email" type="button"
                    class="login__demo-item" @click="fill(d.email)">
              <span class="login__demo-email">{{ d.email }}</span>
              <span class="login__demo-role">{{ d.role }}</span>
            </button>
          </div>
          <div class="login__demo-hint">
            密码统一 <code>123456</code>（由 <code>.env</code> 的
            <code>HUB_AUTH__SEED_PASSWORD</code> 决定）
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: $space-lg;
  background:
    radial-gradient(1200px 600px at 15% 10%, rgba(64, 158, 255, 0.10), transparent 60%),
    radial-gradient(900px 500px at 90% 90%, rgba(103, 194, 58, 0.08), transparent 60%),
    var(--hub-bg-page);

  &__panel {
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    width: min(920px, 100%);
    min-height: 480px;
    overflow: hidden;
    border-radius: $radius-lg;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12);
  }

  // ---------- 左侧品牌 ----------
  &__brand {
    padding: 48px 40px;
    color: #e5eaf3;
    background: linear-gradient(150deg, #1f2d3d 0%, #18222d 60%, #141c24 100%);
    display: flex;
    flex-direction: column;
  }

  &__logo {
    width: 44px;
    height: 44px;
    border-radius: 10px;
    background: $brand;
    color: #fff;
    font-size: 20px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: $space-lg;
  }

  &__title {
    margin: 0 0 $space-sm;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.5px;
  }

  &__sub {
    margin: 0 0 auto;
    font-size: 13px;
    line-height: 1.8;
    color: #a3b1c2;
  }

  &__points {
    margin: $space-lg 0 0;
    padding-left: 18px;
    font-size: 13px;
    line-height: 2;
    color: #b9c6d6;

    strong {
      color: #fff;
    }
  }

  // ---------- 右侧表单 ----------
  &__form {
    padding: 48px 40px;
    background: var(--hub-bg-card);
    display: flex;
    flex-direction: column;
    justify-content: center;
  }

  &__form-title {
    margin: 0 0 $space-lg;
    font-size: 20px;
    font-weight: 600;
  }

  &__submit {
    width: 100%;
    margin-top: $space-sm;
  }

  &__demo {
    margin-top: $space-lg;
    padding-top: $space-md;
    border-top: 1px dashed var(--hub-border-light);
  }

  &__demo-title {
    font-size: 12px;
    color: var(--hub-text-secondary);
    margin-bottom: $space-sm;
  }

  &__demo-list {
    display: flex;
    flex-direction: column;
    gap: $space-xs;
  }

  &__demo-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 6px 10px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    background: transparent;
    cursor: pointer;
    font-size: 12px;
    color: var(--hub-text-regular);
    transition: all $duration-fast;

    &:hover {
      border-color: var(--el-color-primary);
      color: var(--el-color-primary);
      background: var(--el-color-primary-light-9);
    }
  }

  &__demo-email {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  &__demo-role {
    color: var(--hub-text-secondary);
  }

  &__demo-hint {
    margin-top: $space-sm;
    font-size: 11px;
    color: var(--hub-text-secondary);

    code {
      padding: 1px 4px;
      border-radius: 3px;
      background: var(--el-fill-color-light);
    }
  }

  // 窄屏：只留表单（品牌区隐藏，别挤成两行）
  @media (max-width: 860px) {
    &__panel {
      grid-template-columns: 1fr;
    }
    &__brand {
      display: none;
    }
  }
}
</style>
