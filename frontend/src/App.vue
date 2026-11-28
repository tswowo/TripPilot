<script setup>
import { onMounted, ref } from 'vue'

import { api, clearToken, getToken } from './api'
import AuthForm from './components/AuthForm.vue'
import ChatView from './components/ChatView.vue'
import GuideLibraryView from './components/GuideLibraryView.vue'

const user = ref(null)
const checking = ref(true)
const guidesOpen = ref(false)

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
    <div v-if="checking" v-loading="true" class="boot-loading" element-loading-text="正在加载工作台…" />
    <AuthForm v-else-if="!user" @logged-in="user = $event" />
    <template v-else>
      <ChatView :user="user" @logout="logout" @open-guides="guidesOpen = true" />
      <el-drawer
        v-model="guidesOpen"
        :with-header="false"
        size="min(1080px, 95vw)"
        class="guide-drawer"
      >
        <GuideLibraryView v-if="guidesOpen" @close="guidesOpen = false" @logout="logout" />
      </el-drawer>
    </template>
  </main>
</template>

<style>
* {
  box-sizing: border-box;
}
html,
body {
  margin: 0;
  height: 100%;
  font-family:
    'Inter',
    system-ui,
    -apple-system,
    'PingFang SC',
    'Microsoft YaHei',
    sans-serif;
  background: linear-gradient(135deg, #eef3fb 0%, #f6f8fc 100%);
  color: #1f2329;
}
#app {
  height: 100%;
}
.shell {
  max-width: 1260px;
  height: 100vh;
  margin: 0 auto;
  padding: 16px;
}
.boot-loading {
  height: 100%;
  border-radius: 16px;
}
.guide-drawer .el-drawer__body {
  padding: 22px 24px;
}
@media (max-width: 600px) {
  .guide-drawer .el-drawer__body {
    padding: 18px 14px;
  }
}
</style>
