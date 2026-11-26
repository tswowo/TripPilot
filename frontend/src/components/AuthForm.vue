<script setup>
import { ref } from 'vue'

import { api, setToken } from '../api'

const emit = defineEmits(['logged-in'])

const mode = ref('login')
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (busy.value) return
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
  <section class="card">
    <h1>TripPilot</h1>
    <div class="tabs">
      <button :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
      <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
    </div>
    <form @submit.prevent="submit">
      <input v-model="username" placeholder="用户名" autocomplete="username" required />
      <input
        v-model="password"
        type="password"
        placeholder="密码"
        autocomplete="current-password"
        required
      />
      <button type="submit" :disabled="busy">
        {{ busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.card {
  max-width: 360px;
  margin: 80px auto;
  padding: 32px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgb(0 0 0 / 8%);
}
h1 {
  margin: 0 0 20px;
  text-align: center;
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.tabs button {
  flex: 1;
  padding: 8px;
  border: 1px solid #d0d3d9;
  border-radius: 8px;
  background: #fff;
}
.tabs button.active {
  border-color: #3b82f6;
  color: #3b82f6;
  font-weight: 600;
}
form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
input {
  padding: 10px 12px;
  border: 1px solid #d0d3d9;
  border-radius: 8px;
  font-size: 14px;
}
form button {
  padding: 10px;
  border: none;
  border-radius: 8px;
  background: #3b82f6;
  color: #fff;
  font-size: 15px;
}
.error {
  margin: 12px 0 0;
  color: #dc2626;
  font-size: 13px;
}
</style>