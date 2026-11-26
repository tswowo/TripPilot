<script setup>
import { Compass, Lock, User } from '@element-plus/icons-vue'
import { ref } from 'vue'

import { api, setToken } from '../api'

const emit = defineEmits(['logged-in'])

const mode = ref('login')
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (busy.value || !username.value.trim() || !password.value) return
  error.value = ''
  busy.value = true
  try {
    const body = { username: username.value.trim(), password: password.value }
    if (mode.value === 'register') await api('/register', body)
    const { access_token } = await api('/login', body)
    setToken(access_token)
    emit('logged-in', await api('/me'))
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="auth-wrap">
    <el-card class="auth-card" shadow="hover">
      <div class="brand">
        <div class="brand-icon">
          <el-icon :size="26"><Compass /></el-icon>
        </div>
        <div>
          <h1>TripPilot</h1>
          <p>智能旅行规划引擎 · 实时检索与多源核验</p>
        </div>
      </div>

      <el-tabs v-model="mode" stretch class="auth-tabs">
        <el-tab-pane label="账号登录" name="login" />
        <el-tab-pane label="新用户注册" name="register" />
      </el-tabs>

      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input
            v-model="username"
            size="large"
            placeholder="请输入用户名"
            :prefix-icon="User"
            autocomplete="username"
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="password"
            size="large"
            type="password"
            show-password
            placeholder="请输入密码（至少 6 位）"
            :prefix-icon="Lock"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>

        <el-alert
          v-if="error"
          :title="error"
          type="error"
          show-icon
          :closable="false"
          class="auth-error"
        />

        <el-button
          type="primary"
          size="large"
          :loading="busy"
          native-type="submit"
          class="submit-btn"
        >
          {{ mode === 'login' ? '登 录' : '注册并进入' }}
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.auth-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.auth-card {
  width: 400px;
  border-radius: 16px;
  border: 1px solid #e5eaf3;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.brand-icon {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.brand h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
}
.brand p {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: #64748b;
}
.auth-tabs {
  margin-bottom: 8px;
}
.auth-error {
  margin-bottom: 14px;
}
.submit-btn {
  width: 100%;
  border-radius: 10px;
  font-weight: 600;
}
</style>
