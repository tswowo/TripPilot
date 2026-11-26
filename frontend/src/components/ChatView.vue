<script setup>
import { nextTick, onMounted, ref } from 'vue'

import { api } from '../api'

defineProps({ user: Object })
const emit = defineEmits(['logout'])

const STORAGE_KEY = 'trippilot_chat'

const messages = ref([])
const conversationId = ref(null)
const input = ref('')
const busy = ref(false)
const error = ref('')
const listEl = ref(null)

onMounted(() => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY))
    if (saved) {
      messages.value = saved.messages ?? []
      conversationId.value = saved.conversationId ?? null
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
  scrollToEnd()
})

function persist() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ conversationId: conversationId.value, messages: messages.value }),
  )
}

async function scrollToEnd() {
  await nextTick()
  listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  error.value = ''
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  scrollToEnd()
  busy.value = true
  try {
    const data = await api('/chat', { message: text, conversation_id: conversationId.value })
    conversationId.value = data.conversation_id
    messages.value.push({ role: 'assistant', content: data.content })
    persist()
    scrollToEnd()
  } catch (e) {
    if (e.status === 401) {
      emit('logout')
      return
    }
    messages.value.pop()
    input.value = text
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function newConversation() {
  messages.value = []
  conversationId.value = null
  error.value = ''
  localStorage.removeItem(STORAGE_KEY)
}
</script>

<template>
  <section class="chat">
    <header>
      <strong>TripPilot</strong>
      <span class="user">{{ user.username }}</span>
      <button @click="newConversation">新会话</button>
      <button @click="emit('logout')">退出</button>
    </header>

    <div ref="listEl" class="messages">
      <p v-if="messages.length === 0" class="hint">开始你的旅行规划吧，比如：帮我规划杭州两天。</p>
      <div v-for="(m, i) in messages" :key="i" :class="['bubble', m.role]">
        {{ m.content }}
      </div>
      <p v-if="busy" class="hint">思考中…</p>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <form @submit.prevent="send">
      <input v-model="input" placeholder="输入你的旅行需求…" :disabled="busy" />
      <button type="submit" :disabled="busy || !input.trim()">发送</button>
    </form>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 48px);
}
header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
}
header .user {
  flex: 1;
  color: #8a919f;
  font-size: 13px;
}
header button {
  padding: 6px 12px;
  border: 1px solid #d0d3d9;
  border-radius: 8px;
  background: #fff;
}
.messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  background: #fff;
  border-radius: 12px;
}
.bubble {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 12px;
  white-space: pre-wrap;
  line-height: 1.6;
}
.bubble.user {
  align-self: flex-end;
  background: #3b82f6;
  color: #fff;
}
.bubble.assistant {
  align-self: flex-start;
  background: #f0f2f5;
}
form {
  display: flex;
  gap: 8px;
  padding-top: 12px;
}
input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid #d0d3d9;
  border-radius: 8px;
  font-size: 14px;
}
form button {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  background: #3b82f6;
  color: #fff;
}
.error {
  margin: 8px 0 0;
  color: #dc2626;
  font-size: 13px;
}
</style>