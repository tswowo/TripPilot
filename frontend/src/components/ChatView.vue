<script setup>
import {
  ArrowRight,
  ChatDotRound,
  CircleCheck,
  Compass,
  Delete,
  EditPen,
  Loading,
  Opportunity,
  Plus,
  Promotion,
  SwitchButton,
  Tools,
  VideoPause,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, ref } from 'vue'

import { api, apiStream } from '../api'
import MarkdownView from './MarkdownView.vue'

const props = defineProps({ user: Object })
const emit = defineEmits(['logout'])

const activeConvKey = () => `trippilot_active_conv_${props.user.id}`

const QUICK_PROMPTS = [
  '帮我联网查一下南京博物院最新的预约放票时间和闭馆规则',
  '周五晚从上海出发去南京玩周末两天，两人预算 1800 元怎么安排？',
  '苏州园林两日游推荐路线，避开人流高峰怎么走？',
]

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
const thinking = ref(false)
const busy = ref(false)
const error = ref('')

const STICKY_BOTTOM_THRESHOLD = 40

let abortController = null
let switchSeq = 0
let suppressScrollLoad = false
let stickToBottom = true
let lastKnownScrollTop = 0

const currentTitle = computed(() => {
  if (!conversationId.value) return '新旅行规划'
  const hit = conversations.value.find((c) => c.id === conversationId.value)
  return hit?.title || '历史对话'
})

const userInitial = computed(() => (props.user?.username || 'U').slice(0, 1).toUpperCase())

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

async function scrollToEnd(force = false) {
  if (force) {
    stickToBottom = true
  }
  if (!stickToBottom) return
  await nextTick()
  const el = listEl.value
  if (!el || (!force && !stickToBottom)) return
  el.scrollTop = el.scrollHeight
  lastKnownScrollTop = el.scrollTop
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
  if (busy.value) cancelActiveStream()

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
    await scrollToEnd(true)
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

  loadingOlder.value = true
  try {
    const params = new URLSearchParams({
      limit: '20',
      cursor: String(msgNextCursor.value),
    })
    const data = await api(`/conversations/${targetConvId}/messages?${params.toString()}`)
    if (seq !== switchSeq || conversationId.value !== targetConvId) return

    const el = listEl.value
    const prevHeight = el?.scrollHeight ?? 0
    const prevTop = el?.scrollTop ?? 0

    messages.value = [...data.items, ...messages.value]
    msgHasMore.value = data.has_more
    msgNextCursor.value = data.next_cursor

    await nextTick()
    if (el) {
      el.scrollTop = el.scrollHeight - prevHeight + prevTop
      lastKnownScrollTop = el.scrollTop
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
  const currentTop = el.scrollTop
  const distanceFromBottom = el.scrollHeight - currentTop - el.clientHeight

  if (currentTop < lastKnownScrollTop - 2) {
    stickToBottom = false
  } else if (distanceFromBottom <= STICKY_BOTTOM_THRESHOLD) {
    stickToBottom = true
  }
  lastKnownScrollTop = currentTop

  if (currentTop <= 20) {
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

async function promptRename(conv) {
  const targetId = conv?.id ?? conversationId.value
  if (!targetId) return
  const current =
    conv?.title ?? conversations.value.find((c) => c.id === targetId)?.title ?? '新会话'

  let value
  try {
    const res = await ElMessageBox.prompt('请输入新的会话名称', '重命名对话', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: current,
      inputValidator: (v) => {
        const trimmed = (v ?? '').trim()
        if (!trimmed) return '会话名称不能为空'
        if (trimmed.length > 200) return '名称不能超过 200 个字符'
        return true
      },
    })
    value = res.value.trim()
  } catch {
    return
  }

  if (!value || value === current) return

  try {
    const updated = await api(`/conversations/${targetId}`, { title: value }, 'PATCH')
    const hit = conversations.value.find((c) => c.id === targetId)
    if (hit) {
      hit.title = updated.title
    }
    ElMessage.success('会话名称已更新')
  } catch (e) {
    if (e.status === 401) {
      emit('logout')
      return
    }
    ElMessage.error(e.message)
  }
}

async function confirmDelete(conv) {
  const targetId = conv?.id ?? conversationId.value
  if (!targetId) return
  const title =
    conv?.title ?? conversations.value.find((c) => c.id === targetId)?.title ?? '该对话'

  try {
    await ElMessageBox.confirm(`删除「${title}」后历史消息将不再显示，确认删除？`, '删除对话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return
  }

  if (targetId === conversationId.value && busy.value) {
    cancelActiveStream()
  }

  try {
    await api(`/conversations/${targetId}`, undefined, 'DELETE')
    conversations.value = conversations.value.filter((c) => c.id !== targetId)
    if (targetId === conversationId.value) {
      newConversation()
    }
    if (conversations.value.length < 10 && convHasMore.value) {
      loadConversations(false)
    }
    ElMessage.success('对话已删除')
  } catch (e) {
    if (e.status === 401) {
      emit('logout')
      return
    }
    ElMessage.error(e.message)
  }
}

function cancelActiveStream() {
  const ctrl = abortController
  abortController = null
  busy.value = false
  ctrl?.abort()
}

function stopStream() {
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant' && last.status === 'streaming') {
    pauseThinkingTimer(last)
    last.status = 'failed'
    if (!last.content) {
      last.content = '*（已停止生成）*'
    }
  }
  cancelActiveStream()
}

function isActivelyThinking(m, index) {
  return (
    busy.value &&
    index === messages.value.length - 1 &&
    Boolean(m._isThinking)
  )
}

function isReasoningExpanded(m, index) {
  if (typeof m._reasoningOpen === 'boolean') {
    return m._reasoningOpen
  }
  return isActivelyThinking(m, index)
}

function toggleReasoning(m, index) {
  m._reasoningOpen = !isReasoningExpanded(m, index)
}

function pauseThinkingTimer(m) {
  if (m._thinkingStartAt) {
    m._thinkingElapsedMs = (m._thinkingElapsedMs || 0) + (Date.now() - m._thinkingStartAt)
    m._thinkingStartAt = 0
  }
  m._isThinking = false
  if (m._thinkingElapsedMs > 0) {
    m.thinking_seconds = Math.max(1, Math.round(m._thinkingElapsedMs / 1000))
  }
}

function reasoningSummaryLabel(m, index) {
  if (isActivelyThinking(m, index)) {
    return m.thinking_seconds
      ? `深度思考中…（已用时 ${m.thinking_seconds} 秒）`
      : '深度思考中…'
  }
  return m.thinking_seconds ? `已深度思考（用时 ${m.thinking_seconds} 秒）` : '已深度思考'
}

function onInputKeydown(e) {
  // 避开中文输入法候选词回车上屏误触发送
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function applyPrompt(promptText) {
  input.value = promptText
  send()
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  error.value = ''
  input.value = ''
  messages.value.push({ role: 'user', content: text, status: 'completed' })
  messages.value.push({
    role: 'assistant',
    content: '',
    reasoning_content: '',
    steps: [],
    status: 'streaming',
    _thinkingStartAt: 0,
    _thinkingElapsedMs: 0,
    _isThinking: false,
    _needReasoningSep: false,
    _reasoningOpen: undefined,
  })
  const assistantIndex = messages.value.length - 1
  await scrollToEnd(true)

  const controller = new AbortController()
  abortController = controller
  busy.value = true
  const seq = switchSeq

  const appendReasoning = (target, chunkText) => {
    if (!chunkText) return
    const now = Date.now()
    if (!target._thinkingStartAt) {
      target._thinkingStartAt = now
    }
    target._isThinking = true
    const totalMs = (target._thinkingElapsedMs || 0) + (now - target._thinkingStartAt)
    target.thinking_seconds = Math.max(1, Math.round(totalMs / 1000))

    if (target._needReasoningSep && target.reasoning_content) {
      if (!target.reasoning_content.endsWith('\n\n')) {
        target.reasoning_content += '\n\n'
      }
      target._needReasoningSep = false
    }
    target.reasoning_content += chunkText
    scrollToEnd()
  }

  try {
    await apiStream(
      '/chat',
      {
        message: text,
        conversation_id: conversationId.value,
        thinking: thinking.value,
      },
      {
        signal: controller.signal,
        onEvent(event, data) {
          if (seq !== switchSeq || controller.signal.aborted) return
          const curMsg = messages.value[assistantIndex]
          if (event === 'meta' && data.conversation_id) {
            conversationId.value = data.conversation_id
            localStorage.setItem(activeConvKey(), data.conversation_id)
            upsertSidebarConversation(data.conversation_id, data.title || text.slice(0, 50))
          } else if (event === 'step') {
            pauseThinkingTimer(curMsg)
            curMsg.steps.push(data)
            if (curMsg.reasoning_content) {
              curMsg._needReasoningSep = true
            }
            scrollToEnd()
          } else if (event === 'delta') {
            if (data.reasoning_content) {
              appendReasoning(curMsg, data.reasoning_content)
            }
            if (data.content) {
              pauseThinkingTimer(curMsg)
              curMsg.content += data.content
              scrollToEnd()
            }
          } else if (event === 'error' && data.message) {
            pauseThinkingTimer(curMsg)
            curMsg.status = 'failed'
            curMsg.error_message = data.message
            error.value = data.message
          } else if (event === 'done') {
            pauseThinkingTimer(curMsg)
            curMsg.status = 'completed'
          }
        },
      },
    )
    if (seq === switchSeq) await scrollToEnd()
  } catch (e) {
    if (seq !== switchSeq) return
    if (e.name === 'AbortError' || controller.signal.aborted) {
      const target = messages.value[assistantIndex]
      if (target) {
        pauseThinkingTimer(target)
        target.status = 'failed'
        if (!target.content) {
          target.content = '*（已停止生成）*'
        }
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
    if (abortController === controller) {
      busy.value = false
      abortController = null
    }
  }
}

function newConversation() {
  if (busy.value) cancelActiveStream()
  switchSeq++
  stickToBottom = true
  lastKnownScrollTop = 0
  conversationId.value = null
  messages.value = []
  msgNextCursor.value = null
  msgHasMore.value = false
  error.value = ''
  localStorage.removeItem(activeConvKey())
}

function fallbackText(m) {
  if (m.status === 'failed') return m.error_message ? `*（${m.error_message}）*` : '*（已停止生成）*'
  if (m.status === 'streaming') return '*（正在生成或已中断…）*'
  return ''
}
</script>

<template>
  <div class="workspace">
    <!-- 左侧会话导航栏 -->
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="logo-badge">
          <el-icon :size="18"><Compass /></el-icon>
        </div>
        <span class="brand-name">TripPilot</span>
      </div>

      <el-button type="primary" plain :icon="Plus" class="new-conv-btn" @click="newConversation">
        新建旅行规划
      </el-button>

      <div ref="convListEl" class="conv-list" @scroll="onConvScroll">
        <el-empty
          v-if="!loadingConvs && conversations.length === 0"
          :image-size="56"
          description="暂无历史对话"
        />
        <div
          v-for="c in conversations"
          :key="c.id"
          :class="['conv-item', { active: c.id === conversationId }]"
          @click="selectConversation(c.id)"
        >
          <el-icon class="conv-icon"><ChatDotRound /></el-icon>
          <span class="conv-title" :title="c.title">{{ c.title }}</span>
          <span class="conv-actions">
            <button
              type="button"
              class="action-icon-btn"
              title="重命名会话"
              @click.stop="promptRename(c)"
            >
              <el-icon><EditPen /></el-icon>
            </button>
            <button
              type="button"
              class="action-icon-btn danger"
              title="删除会话"
              @click.stop="confirmDelete(c)"
            >
              <el-icon><Delete /></el-icon>
            </button>
          </span>
          <span class="conv-time">{{ formatShortTime(c.update_time) }}</span>
        </div>
        <p v-if="loadingConvs" class="side-hint">加载中…</p>
      </div>

      <div class="sidebar-footer">
        <div class="user-info">
          <el-avatar :size="28" class="user-avatar">{{ userInitial }}</el-avatar>
          <span class="username" :title="user.username">{{ user.username }}</span>
        </div>
        <el-button text size="small" :icon="SwitchButton" @click="emit('logout')">退出</el-button>
      </div>
    </aside>

    <!-- 右侧聊天主面板 -->
    <section class="chat-panel">
      <header class="chat-header">
        <div class="header-title">
          <span class="header-title-text">{{ currentTitle }}</span>
          <el-button
            v-if="conversationId"
            text
            circle
            size="small"
            :icon="EditPen"
            title="重命名当前会话"
            @click="promptRename()"
          />
          <el-button
            v-if="conversationId"
            text
            circle
            size="small"
            type="danger"
            :icon="Delete"
            title="删除当前会话"
            @click="confirmDelete()"
          />
          <el-tag v-if="busy" size="small" type="primary" effect="light" round class="status-tag">
            生成中…
          </el-tag>
        </div>
      </header>

      <div ref="listEl" v-loading="loadingMsgs" class="messages" @scroll="onMsgScroll">
        <p v-if="loadingOlder" class="top-hint">正在加载更早记录…</p>
        <p v-else-if="messages.length > 0 && !msgHasMore" class="top-hint">已到对话顶部</p>

        <!-- 空会话引导页 -->
        <div v-if="!loadingMsgs && messages.length === 0" class="welcome">
          <div class="welcome-icon">
            <el-icon :size="32"><Compass /></el-icon>
          </div>
          <h2>下一站想去哪里？</h2>
          <p>支持联网检索最新景区公告，后续将接入高德路网与美团实时票务酒旅核验</p>
          <div class="prompt-grid">
            <button
              v-for="p in QUICK_PROMPTS"
              :key="p"
              type="button"
              class="prompt-chip"
              @click="applyPrompt(p)"
            >
              {{ p }}
            </button>
          </div>
        </div>

        <!-- 消息流 -->
        <div v-for="(m, i) in messages" :key="m.id ?? `tmp-${i}`" :class="['msg-row', m.role]">
          <el-avatar v-if="m.role === 'assistant'" :size="32" class="ai-avatar">
            <el-icon :size="17"><Compass /></el-icon>
          </el-avatar>

          <div :class="['bubble', m.role]">
            <!-- DeepSeek 深度思考链 -->
            <div v-if="m.role === 'assistant' && m.reasoning_content" class="reasoning-card">
              <button
                type="button"
                class="reasoning-header"
                @click="toggleReasoning(m, i)"
              >
                <el-icon v-if="isActivelyThinking(m, i)" class="is-loading reasoning-icon active">
                  <Loading />
                </el-icon>
                <el-icon v-else class="reasoning-icon"><Opportunity /></el-icon>
                <span>{{ reasoningSummaryLabel(m, i) }}</span>
                <el-icon
                  :class="['reasoning-arrow', { expanded: isReasoningExpanded(m, i) }]"
                >
                  <ArrowRight />
                </el-icon>
              </button>
              <div v-show="isReasoningExpanded(m, i)" class="reasoning-body">
                {{ m.reasoning_content }}
              </div>
            </div>

            <!-- Agent 工具与节点执行轨迹 -->
            <details v-if="m.steps?.length" class="steps-card">
              <summary>
                <el-icon v-if="busy && i === messages.length - 1" class="is-loading step-icon">
                  <Loading />
                </el-icon>
                <el-icon v-else class="step-icon done"><CircleCheck /></el-icon>
                <span>已执行 {{ m.steps.length }} 个推理/工具步骤</span>
              </summary>
              <div class="steps-list">
                <div v-for="s in m.steps" :key="s.step" class="step-row">
                  <span class="step-idx">#{{ s.step }}</span>
                  <el-tag size="small" type="info" effect="plain">{{ s.node }}</el-tag>
                  <template v-if="s.tools">
                    <span class="step-desc">调用</span>
                    <el-tag
                      v-for="t in s.tools"
                      :key="t"
                      size="small"
                      type="primary"
                      effect="light"
                    >
                      <el-icon style="margin-right: 3px"><Tools /></el-icon>{{ t }}
                    </el-tag>
                  </template>
                  <template v-else-if="s.results">
                    <span class="step-desc">返回</span>
                    <el-tag
                      v-for="(r, ri) in s.results"
                      :key="ri"
                      size="small"
                      :type="r.status === 'error' ? 'danger' : 'success'"
                      effect="light"
                    >
                      {{ r.tool }} ({{ r.status === 'error' ? '异常重试' : '成功' }})
                    </el-tag>
                  </template>
                  <span v-else class="step-desc">节点完成</span>
                </div>
              </div>
            </details>

            <!-- 用户纯文本 / AI Markdown 渲染 -->
            <div v-if="m.role === 'user'" class="user-text">{{ m.content }}</div>
            <template v-else>
              <div
                v-if="!m.content && !m.reasoning_content && busy && i === messages.length - 1"
                class="thinking"
              >
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>正在思考与检索信息…</span>
              </div>
              <MarkdownView
                v-else-if="m.content || !busy || i !== messages.length - 1"
                :content="m.content || fallbackText(m)"
              />
            </template>
          </div>

          <el-avatar v-if="m.role === 'user'" :size="32" class="user-avatar">
            {{ userInitial }}
          </el-avatar>
        </div>
      </div>

      <!-- 底部输入区 -->
      <footer class="composer">
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          show-icon
          closable
          class="composer-error"
          @close="error = ''"
        />
        <div class="composer-box">
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            placeholder="输入你的旅行需求（Enter 发送，Shift + Enter 换行）…"
            :disabled="loadingMsgs"
            class="composer-input"
            @keydown="onInputKeydown"
          />
          <button
            type="button"
            :class="['think-toggle', { active: thinking }]"
            title="开启/关闭深度思考"
            @click="thinking = !thinking"
          >
            <el-icon><Opportunity /></el-icon>
            <span>深度思考</span>
          </button>
          <el-button
            v-if="busy"
            type="danger"
            :icon="VideoPause"
            round
            class="action-btn"
            @click="stopStream"
          >
            停止
          </el-button>
          <el-button
            v-else
            type="primary"
            :icon="Promotion"
            round
            class="action-btn"
            :disabled="loadingMsgs || !input.trim()"
            @click="send"
          >
            发送
          </el-button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.workspace {
  display: flex;
  gap: 16px;
  height: 100%;
}

/* 左侧边栏 */
.sidebar {
  width: 272px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e5eaf3;
  border-radius: 16px;
  padding: 16px 12px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 6px 14px;
}

.logo-badge {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-name {
  font-size: 17px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.2px;
}

.new-conv-btn {
  width: 100%;
  border-radius: 10px;
  font-weight: 500;
  margin-bottom: 10px;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-right: 2px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 10px;
  cursor: pointer;
  color: #334155;
  transition: all 0.15s;
}

.conv-item:hover {
  background: #f1f5f9;
}

.conv-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 500;
}

.conv-icon {
  flex-shrink: 0;
  font-size: 15px;
  opacity: 0.75;
}

.conv-title {
  flex: 1;
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  flex-shrink: 0;
  font-size: 11px;
  color: #94a3b8;
}

.conv-actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.action-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.action-icon-btn:hover {
  background: #e2e8f0;
  color: var(--el-color-primary);
}

.action-icon-btn.danger:hover {
  background: #fee2e2;
  color: var(--el-color-danger);
}

.conv-item:hover .conv-actions {
  display: inline-flex;
}

.conv-item:hover .conv-time {
  display: none;
}

.side-hint {
  margin: 10px 0;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
}

.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 4px 0;
  margin-top: 8px;
  border-top: 1px solid #eef2f7;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.user-avatar {
  background: #3b82f6;
  color: #fff;
  font-weight: 600;
  flex-shrink: 0;
}

.username {
  font-size: 13.5px;
  font-weight: 500;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧聊天主面板 */
.chat-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e5eaf3;
  border-radius: 16px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
  overflow: hidden;
}

.chat-header {
  padding: 13px 20px;
  border-bottom: 1px solid #eef2f7;
  background: #fafbfe;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  min-width: 0;
}

.header-title-text {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.top-hint {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
}

/* 欢迎空状态 */
.welcome {
  margin: auto;
  max-width: 560px;
  text-align: center;
  padding: 24px 12px;
}

.welcome-icon {
  width: 58px;
  height: 58px;
  margin: 0 auto 14px;
  border-radius: 16px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.welcome h2 {
  margin: 0 0 8px;
  font-size: 20px;
  color: #0f172a;
}

.welcome p {
  margin: 0 0 20px;
  font-size: 13.5px;
  color: #64748b;
}

.prompt-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.prompt-chip {
  text-align: left;
  padding: 11px 15px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
  color: #334155;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s;
}

.prompt-chip:hover {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

/* 消息行与气泡 */
.msg-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.msg-row.user {
  justify-content: flex-end;
}

.ai-avatar {
  background: linear-gradient(135deg, #2563eb, #0ea5e9);
  color: #fff;
  flex-shrink: 0;
  margin-top: 2px;
}

.bubble {
  max-width: min(82%, 780px);
  border-radius: 14px;
  padding: 12px 16px;
}

.bubble.user {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 8px rgb(37 99 235 / 18%);
}

.user-text {
  white-space: pre-wrap;
  line-height: 1.65;
  font-size: 14.5px;
  word-break: break-word;
}

.bubble.assistant {
  background: #f8fafc;
  border: 1px solid #e8eef5;
  border-top-left-radius: 4px;
}

/* 深度思考链卡片 */
.reasoning-card {
  margin-bottom: 10px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  overflow: hidden;
}

.reasoning-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 11px;
  border: none;
  background: transparent;
  color: #475569;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
}

.reasoning-header:hover {
  background: #e8eef5;
}

.reasoning-icon {
  font-size: 14px;
  color: #64748b;
}

.reasoning-icon.active {
  color: var(--el-color-primary);
}

.reasoning-arrow {
  margin-left: auto;
  font-size: 12px;
  color: #94a3b8;
  transition: transform 0.2s ease;
}

.reasoning-arrow.expanded {
  transform: rotate(90deg);
}

.reasoning-body {
  padding: 2px 12px 10px 14px;
  margin-left: 11px;
  margin-bottom: 8px;
  border-left: 2px solid #cbd5e1;
  font-size: 13px;
  line-height: 1.68;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 推理与工具调用折叠条 */
.steps-card {
  margin-bottom: 10px;
  padding: 7px 11px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  font-size: 12.5px;
  color: #475569;
}

.steps-card summary {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  font-weight: 500;
  list-style: none;
}

.steps-card summary::-webkit-details-marker {
  display: none;
}

.step-icon {
  font-size: 14px;
  color: var(--el-color-primary);
}

.step-icon.done {
  color: var(--el-color-success);
}

.steps-list {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.step-idx {
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
  color: #94a3b8;
}

.step-desc {
  color: #64748b;
  font-size: 12px;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 13.5px;
  padding: 2px 0;
}

/* 底部输入区 */
.composer {
  padding: 12px 20px 16px;
  border-top: 1px solid #eef2f7;
  background: #fff;
}

.composer-error {
  margin-bottom: 10px;
}

.composer-box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 8px 10px 8px 14px;
  border: 1px solid #dbe2ee;
  border-radius: 14px;
  background: #f8fafc;
  transition:
    border-color 0.15s,
    background 0.15s,
    box-shadow 0.15s;
}

.composer-box:focus-within {
  background: #fff;
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 3px rgb(59 130 246 / 10%);
}

.composer-input :deep(.el-textarea__inner) {
  padding: 4px 0;
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  font-size: 14.5px;
  line-height: 1.6;
}

.action-btn {
  flex-shrink: 0;
  padding: 0 18px;
  height: 36px;
  font-weight: 500;
}

.think-toggle {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 36px;
  padding: 0 13px;
  border: 1px solid #dbe2ee;
  border-radius: 18px;
  background: #fff;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.think-toggle:hover {
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}

.think-toggle.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
</style>
