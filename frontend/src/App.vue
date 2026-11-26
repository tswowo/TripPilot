<script setup>
import { onMounted, ref } from 'vue'

import { api, clearToken, getToken } from './api'
import AuthForm from './components/AuthForm.vue'
import ChatView from './components/ChatView.vue'

const user = ref(null)
const checking = ref(true)

onMounted(async () => {
  if (getToken()) {
    try {
      user.value = await api('/me')
    } catch {
      clearToken()
    }
  }
  checking.value = false
})

function logout() {
  clearToken()
  user.value = null
}
</script>

<template>
  <main class="shell">
    <p v-if="checking" class="hint">加载中…</p>
    <AuthForm v-else-if="!user" @logged-in="user = $event" />
    <ChatView v-else :user="user" @logout="logout" />
  </main>
</template>

<style>
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  font-family: system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: #f5f6f8;
  color: #1f2329;
}
.shell {
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 16px;
}
.hint {
  color: #8a919f;
  text-align: center;
}
button {
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>