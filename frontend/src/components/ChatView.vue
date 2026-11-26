<script setup>
import { nextTick, onMounted, ref } from 'vue'

import { apiStream } from '../api'

defineProps({ user: Object })
const emit = defineEmits(['logout'])

const STORAGE_KEY = 'trippilot_chat'

const messages = ref([])
const conversationId = ref(null)
const input = ref('')
const busy = ref(false)
const error = ref('')
const listEl = ref(null)
let abortController = null

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

function stopStream() {
  abortController?.abort()
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  error.value = ''
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  messages.value.push({ role: 'assistant', content: '', steps: [] })
  const assistantIndex = messages.value.length - 1
  scrollToEnd()
  busy.value = true
  abortController = new AbortController()

  try {
    await apiStream(
      '/chat',
      { message: text, conversation_id: conversationId.value },
      {
        signal: abortController.signal,
        onEvent(event, data) {
          if (event === 'meta' && data.conversation_id) {
            conversationId.value = data.conversation_id
            persist()
          } else if (event === 'step') {
            messages.value[assistantIndex].steps.push(data)
            scrollToEnd()
          } else if (event === 'delta' && data.content) {
            messages.value[assistantIndex].content += data.content
            scrollToEnd()
          } else if (event === 'error' && data.message) {
            error.value = data.message
          } else if (event === 'done') {
            persist()
          }
        },
      },
    )
    persist()
    scrollToEnd()
  } catch (e) {
    if (e.name === 'AbortError') {
      if (!messages.value[assistantIndex].content) {
        messages.value[assistantIndex].content = '（已停止生成）'
      }
      persist()
      return
    }
    if (e.status === 401) {
      emit('logout')
      return
    }
    // 请求在建立流之前失败（如 404），回滚刚塞入的 user 与空 assistant 气泡
    messages.value.splice(assistantIndex - 1, 2)
    input.value = text
    error.value = e.message
  } finally {
    busy.value = false
    abortController = null
  }
}

function newConversation() {
  abortController?.abort()
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
        <details v-if="m.steps?.length" class="steps-box">
          <summary>已执行 {{ m.steps.length }} 个步骤</summary>
          <ul>
            <li v-for="s in m.steps" :key="s.step">
              #{{ s.step }} [{{ s.node }}]
              <span v-if="s.tools">调用工具: {{ s.tools.join(', ') }}</span>
              <span v-else-if="s.results">工具返回: {{ s.results.map((r) => r.tool).join(', ') }}</span>
              <span v-else>节点完成</span>
            </li>
          </ul>
        </details>
        <div>{{ m.content || (busy && i === messages.length - 1 ? '思考中…' : '') }}</div>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <form @submit.prevent="send">
      <input v-model="input" placeholder="输入你的旅行需求…" :disabled="busy" />
      <button v-if="busy" type="button" class="stop-btn" @click="stopStream">停止</button>
      <button v-else type="submit" :disabled="!input.trim()">发送</button>
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
.steps-box {
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px dashed #d0d3d9;
  font-size: 12px;
  color: #646a73;
}
.steps-box summary {
  cursor: pointer;
  user-select: none;
}
.steps-box ul {
  margin: 4px 0 0;
  padding-left: 18px;
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
form button.stop-btn {
  background: #ef4444;
}
.error {
  margin: 8px 0 0;
  color: #dc2626;
  font-size: 13px;
}
</style>
