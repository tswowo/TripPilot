<script setup>
import { nextTick, onMounted, ref } from 'vue'

import { api, apiStream } from '../api'

const props = defineProps({ user: Object })
const emit = defineEmits(['logout'])

const activeConvKey = () => `trippilot_active_conv_${props.user.id}`

// 左侧会话列表游标分页状态
const conversations = ref([])
const convNextCursor = ref(null)
const convHasMore = ref(false)
const loadingConvs = ref(false)
const convListEl = ref(null)

// 右侧当前会话与消息游标分页状态
const conversationId = ref(null)
const messages = ref([])
const msgNextCursor = ref(null)
const msgHasMore = ref(false)
const loadingMsgs = ref(false)
const loadingOlder = ref(false)
const listEl = ref(null)

const input = ref('')
const busy = ref(false)
const error = ref('')

let abortController = null
let switchSeq = 0
let suppressScrollLoad = false

onMounted(async () => {
  localStorage.removeItem('trippilot_chat')
  await loadConversations(true)
  const savedId = localStorage.getItem(activeConvKey())
  if (savedId) {
    await selectConversation(savedId)
  }
})

function formatShortTime(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}

async function scrollToEnd() {
  await nextTick()
  if (listEl.value) {
    listEl.value.scrollTop = listEl.value.scrollHeight
  }
}

async function loadConversations(reset = false) {
  if (loadingConvs.value) return
  if (!reset && !convHasMore.value) return
  loadingConvs.value = true
  try {
    const params = new URLSearchParams({ limit: '20' })
    if (!reset && convNextCursor.value) {
      params.set('cursor', convNextCursor.value)
    }
    const data = await api(`/conversations?${params.toString()}`)
    if (reset) {
      conversations.value = data.items
    } else {
      const seen = new Set(conversations.value.map((c) => c.id))
      conversations.value.push(...data.items.filter((c) => !seen.has(c.id)))
    }
    convHasMore.value = data.has_more
    convNextCursor.value = data.next_cursor
  } catch (e) {
    if (e.status === 401) {
      emit('logout')
      return
    }
    error.value = e.message
  } finally {
    loadingConvs.value = false
  }
}

function onConvScroll() {
  const el = convListEl.value
  if (!el || loadingConvs.value || !convHasMore.value) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 30) {
    loadConversations(false)
  }
}

async function selectConversation(id) {
  if (id === conversationId.value && !error.value && messages.value.length > 0) return
  if (busy.value) abortController?.abort()

  const seq = ++switchSeq
  conversationId.value = id
  localStorage.setItem(activeConvKey(), id)
  messages.value = []
  msgNextCursor.value = null
  msgHasMore.value = false
  error.value = ''
  loadingMsgs.value = true
  suppressScrollLoad = true

  try {
    const data = await api(`/conversations/${id}/messages?limit=20`)
    if (seq !== switchSeq) return
    messages.value = data.items
    msgHasMore.value = data.has_more
    msgNextCursor.value = data.next_cursor
    await scrollToEnd()
  } catch (e) {
    if (seq !== switchSeq) return
    if (e.status === 401) {
      emit('logout')
      return
    }
    if (e.status === 404) {
      newConversation()
      return
    }
    error.value = e.message
  } finally {
    if (seq === switchSeq) {
      loadingMsgs.value = false
      suppressScrollLoad = false
    }
  }
}

async function loadOlderMessages() {
  if (
    suppressScrollLoad ||
    loadingMsgs.value ||
    loadingOlder.value ||
    !msgHasMore.value ||
    !msgNextCursor.value ||
    !conversationId.value
  ) {
    return
  }

  const seq = switchSeq
  const targetConvId = conversationId.value
  const el = listEl.value
  const prevHeight = el?.scrollHeight ?? 0
  const prevTop = el?.scrollTop ?? 0

  loadingOlder.value = true
  try {
    const params = new URLSearchParams({
      limit: '20',
      cursor: String(msgNextCursor.value),
    })
    const data = await api(`/conversations/${targetConvId}/messages?${params.toString()}`)
    if (seq !== switchSeq || conversationId.value !== targetConvId) return

    messages.value = [...data.items, ...messages.value]
    msgHasMore.value = data.has_more
    msgNextCursor.value = data.next_cursor

    // 视口锚定：抵消头部插入历史消息带来的 scrollHeight 增量，保持阅读视线零闪跳
    await nextTick()
    if (el) {
      el.scrollTop = el.scrollHeight - prevHeight + prevTop
    }
  } catch (e) {
    if (e.status === 401) {
      emit('logout')
      return
    }
    if (seq === switchSeq) error.value = e.message
  } finally {
    if (seq === switchSeq) loadingOlder.value = false
  }
}

function onMsgScroll() {
  const el = listEl.value
  if (!el) return
  if (el.scrollTop <= 20) {
    loadOlderMessages()
  }
}

function upsertSidebarConversation(id, fallbackTitle) {
  const nowIso = new Date().toISOString()
  const idx = conversations.value.findIndex((c) => c.id === id)
  if (idx >= 0) {
    const [existing] = conversations.value.splice(idx, 1)
    conversations.value.unshift({ ...existing, update_time: nowIso })
  } else {
    conversations.value.unshift({
      id,
      title: fallbackTitle || '新会话',
      update_time: nowIso,
    })
  }
}

function stopStream() {
  abortController?.abort()
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  error.value = ''
  input.value = ''
  messages.value.push({ role: 'user', content: text, status: 'completed' })
  messages.value.push({ role: 'assistant', content: '', steps: [], status: 'streaming' })
  const assistantIndex = messages.value.length - 1
  await scrollToEnd()

  busy.value = true
  abortController = new AbortController()
  const seq = switchSeq

  try {
    await apiStream(
      '/chat',
      { message: text, conversation_id: conversationId.value },
      {
        signal: abortController.signal,
        onEvent(event, data) {
          if (seq !== switchSeq) return
          if (event === 'meta' && data.conversation_id) {
            conversationId.value = data.conversation_id
            localStorage.setItem(activeConvKey(), data.conversation_id)
            upsertSidebarConversation(data.conversation_id, data.title || text.slice(0, 50))
          } else if (event === 'step') {
            messages.value[assistantIndex].steps.push(data)
            scrollToEnd()
          } else if (event === 'delta' && data.content) {
            messages.value[assistantIndex].content += data.content
            scrollToEnd()
          } else if (event === 'error' && data.message) {
            messages.value[assistantIndex].status = 'failed'
            messages.value[assistantIndex].error_message = data.message
            error.value = data.message
          } else if (event === 'done') {
            messages.value[assistantIndex].status = 'completed'
          }
        },
      },
    )
    if (seq === switchSeq) await scrollToEnd()
  } catch (e) {
    if (seq !== switchSeq) return
    if (e.name === 'AbortError') {
      messages.value[assistantIndex].status = 'failed'
      if (!messages.value[assistantIndex].content) {
        messages.value[assistantIndex].content = '（已停止生成）'
      }
      return
    }
    if (e.status === 401) {
      emit('logout')
      return
    }
    messages.value.splice(assistantIndex - 1, 2)
    input.value = text
    error.value = e.message
  } finally {
    if (seq === switchSeq) {
      busy.value = false
      abortController = null
    }
  }
}

function newConversation() {
  if (busy.value) abortController?.abort()
  switchSeq++
  conversationId.value = null
  messages.value = []
  msgNextCursor.value = null
  msgHasMore.value = false
  error.value = ''
  localStorage.removeItem(activeConvKey())
}

function displayContent(m, index) {
  if (m.content) return m.content
  if (busy.value && index === messages.value.length - 1) return '思考中…'
  if (m.status === 'failed') return m.error_message ? `（${m.error_message}）` : '（已停止生成）'
  return ''
}
</script>

<template>
  <div class="workspace">
    <!-- 左侧会话列表栏 -->
    <aside class="sidebar">
      <div class="sidebar-top">
        <strong>TripPilot</strong>
        <button class="new-btn" @click="newConversation">+ 新会话</button>
      </div>

      <div ref="convListEl" class="conv-list" @scroll="onConvScroll">
        <p v-if="!loadingConvs && conversations.length === 0" class="side-hint">暂无历史会话</p>
        <div
          v-for="c in conversations"
          :key="c.id"
          :class="['conv-item', { active: c.id === conversationId }]"
          @click="selectConversation(c.id)"
        >
          <span class="conv-title" :title="c.title">{{ c.title }}</span>
          <span class="conv-time">{{ formatShortTime(c.update_time) }}</span>
        </div>
        <p v-if="loadingConvs" class="side-hint">加载中…</p>
      </div>

      <div class="sidebar-footer">
        <span class="user">{{ user.username }}</span>
        <button class="logout-btn" @click="emit('logout')">退出</button>
      </div>
    </aside>

    <!-- 右侧聊天主视口 -->
    <section class="chat">
      <div ref="listEl" class="messages" @scroll="onMsgScroll">
        <p v-if="loadingOlder" class="top-hint">加载更早消息中…</p>
        <p v-else-if="messages.length > 0 && !msgHasMore" class="top-hint">已加载全部历史消息</p>

        <p v-if="loadingMsgs" class="hint">正在加载会话记录…</p>
        <p v-else-if="messages.length === 0" class="hint">
          开始你的旅行规划吧，比如：帮我规划杭州两天。
        </p>

        <div v-for="(m, i) in messages" :key="m.id ?? `tmp-${i}`" :class="['bubble', m.role]">
          <details v-if="m.steps?.length" class="steps-box">
            <summary>已执行 {{ m.steps.length }} 个步骤</summary>
            <ul>
              <li v-for="s in m.steps" :key="s.step">
                #{{ s.step }} [{{ s.node }}]
                <span v-if="s.tools">调用工具: {{ s.tools.join(', ') }}</span>
                <span v-else-if="s.results">
                  工具返回: {{ s.results.map((r) => r.tool).join(', ') }}
                </span>
                <span v-else>节点完成</span>
              </li>
            </ul>
          </details>
          <div>{{ displayContent(m, i) }}</div>
        </div>
      </div>

      <p v-if="error" class="error">{{ error }}</p>
      <form @submit.prevent="send">
        <input v-model="input" placeholder="输入你的旅行需求…" :disabled="busy || loadingMsgs" />
        <button v-if="busy" type="button" class="stop-btn" @click="stopStream">停止</button>
        <button v-else type="submit" :disabled="loadingMsgs || !input.trim()">发送</button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.workspace {
  display: flex;
  gap: 16px;
  height: calc(100vh - 40px);
}

/* 左侧边栏 */
.sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  padding: 14px 12px;
}
.sidebar-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 12px;
  border-bottom: 1px solid #eef0f4;
}
.new-btn {
  padding: 6px 10px;
  border: 1px solid #3b82f6;
  border-radius: 8px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 13px;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  margin: 10px 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.conv-item:hover {
  background: #f5f6f8;
}
.conv-item.active {
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 500;
}
.conv-title {
  flex: 1;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-time {
  flex-shrink: 0;
  font-size: 11px;
  color: #9ca3af;
}
.side-hint {
  margin: 12px 0;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}
.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 4px 0;
  border-top: 1px solid #eef0f4;
}
.sidebar-footer .user {
  font-size: 13px;
  color: #646a73;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.logout-btn {
  padding: 5px 10px;
  border: 1px solid #d0d3d9;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
}

/* 右侧聊天主视口 */
.chat {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
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
.top-hint {
  margin: 0 0 4px;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
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
