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
    messages.value = (data.items || []).map(hydrateMessageBlocks)
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

    messages.value = [...(data.items || []).map(hydrateMessageBlocks), ...messages.value]
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

function hydrateMessageBlocks(rawMsg) {
  if (!rawMsg || rawMsg.role !== 'assistant') {
    return rawMsg
  }
  const steps = Array.isArray(rawMsg.steps) ? rawMsg.steps : []
  const isTimelineFormat = steps.length > 0 && typeof steps[0]?.type === 'string'

  const blocks = []
  if (isTimelineFormat) {
    for (const b of steps) {
      if (b.type === 'reasoning' && b.text) {
        blocks.push({
          type: 'reasoning',
          text: b.text,
          duration_s: b.duration_s || 0,
          _isThinking: false,
          _open: false,
          _userToggled: false,
        })
      } else if (b.type === 'tool_round') {
        blocks.push({
          type: 'tool_round',
          round: b.round || 1,
          duration_ms: b.duration_ms ?? null,
          calls: Array.isArray(b.calls) ? b.calls : [],
          _open: false,
        })
      } else if (b.type === 'content' && b.text) {
        blocks.push({
          type: 'content',
          text: b.text,
        })
      }
    }
  } else {
    // 兼容旧版未切块历史会话数据
    if (rawMsg.reasoning_content) {
      blocks.push({
        type: 'reasoning',
        text: rawMsg.reasoning_content,
        duration_s: rawMsg.thinking_seconds || 0,
        _isThinking: false,
        _open: false,
        _userToggled: false,
      })
    }
    let legacyRound = 0
    let activeLegacyRound = null
    for (const s of steps) {
      if (s.action === 'call_tools' || Array.isArray(s.tools)) {
        legacyRound++
        const calls = (s.calls || (s.tools || []).map((t, idx) => ({ id: `${legacyRound}_${idx}`, name: t, summary: '' }))).map(
          (c, idx) => ({
            id: c.id || `${legacyRound}_${idx}`,
            name: c.name || 'unknown',
            summary: c.summary || '',
            status: 'success',
          }),
        )
        activeLegacyRound = {
          type: 'tool_round',
          round: legacyRound,
          start_ts_ms: s.ts_ms || 0,
          duration_ms: null,
          calls,
          _open: false,
        }
        blocks.push(activeLegacyRound)
      } else if (s.action === 'tool_results' && activeLegacyRound) {
        if (s.ts_ms && activeLegacyRound.start_ts_ms) {
          activeLegacyRound.duration_ms = Math.max(0, s.ts_ms - activeLegacyRound.start_ts_ms)
        }
        for (const r of s.results || []) {
          const hit = activeLegacyRound.calls.find((c) => (r.id && c.id === r.id) || c.name === r.tool)
          if (hit) hit.status = r.status || 'success'
        }
      }
    }
    if (rawMsg.content) {
      blocks.push({
        type: 'content',
        text: rawMsg.content,
      })
    }
  }

  return {
    ...rawMsg,
    blocks,
  }
}

function pauseActiveReasoning(m) {
  const blk = m?._activeReasoningBlock
  if (!blk) return
  if (blk._startAt) {
    blk._elapsedMs = (blk._elapsedMs || 0) + (Date.now() - blk._startAt)
    blk._startAt = 0
  }
  blk._isThinking = false
  if (blk._elapsedMs > 0) {
    blk.duration_s = Math.max(1, Math.round(blk._elapsedMs / 1000))
  }
  if (!blk._userToggled) {
    blk._open = false
  }
}

function finalizeMessageStream(m, { cancelled = false } = {}) {
  if (!m) return
  pauseActiveReasoning(m)
  m._activeReasoningBlock = null
  m._activeToolRoundBlock = null
  if (cancelled && Array.isArray(m.blocks)) {
    for (const b of m.blocks) {
      if (b.type === 'tool_round' && Array.isArray(b.calls)) {
        for (const c of b.calls) {
          if (c.status === 'running') {
            c.status = 'cancelled'
          }
        }
      }
    }
  }
}

function stopStream() {
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant' && last.status === 'streaming') {
    finalizeMessageStream(last, { cancelled: true })
    last.status = 'failed'
    if (!last.content) {
      last.content = '*（已停止生成）*'
    }
  }
  cancelActiveStream()
}

function toggleReasoningBlock(block) {
  block._open = !block._open
  block._userToggled = true
}

function reasoningBlockLabel(block) {
  if (block._isThinking) {
    return block.duration_s
      ? `深度思考中…（已用时 ${block.duration_s} 秒）`
      : '深度思考中…'
  }
  return block.duration_s ? `已深度思考（用时 ${block.duration_s} 秒）` : '已深度思考'
}

function toggleToolRoundBlock(block) {
  block._open = !block._open
}

function isToolRoundRunning(block) {
  return Array.isArray(block.calls) && block.calls.some((c) => c.status === 'running')
}

function formatDurationMs(ms) {
  if (ms == null || Number.isNaN(ms) || ms <= 0) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function toolStatusLabel(status) {
  if (status === 'running') return '执行中…'
  if (status === 'error') return '异常重试'
  if (status === 'cancelled') return '已中断'
  return '完成'
}

function toolStatusTagType(status) {
  if (status === 'running') return 'primary'
  if (status === 'error') return 'danger'
  if (status === 'cancelled') return 'info'
  return 'success'
}

function hasContentBlock(m) {
  return Array.isArray(m.blocks) && m.blocks.some((b) => b.type === 'content' && b.text)
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
    blocks: [],
    status: 'streaming',
    _activeReasoningBlock: null,
    _activeToolRoundBlock: null,
    _toolRoundSeq: 0,
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
    const lastBlock = target.blocks[target.blocks.length - 1]
    if (!target._activeReasoningBlock || !lastBlock || lastBlock.type !== 'reasoning') {
      const newBlock = {
        type: 'reasoning',
        text: '',
        duration_s: 1,
        _startAt: now,
        _elapsedMs: 0,
        _isThinking: true,
        _open: true,
        _userToggled: false,
      }
      target.blocks.push(newBlock)
      target._activeReasoningBlock = newBlock
    }
    const blk = target._activeReasoningBlock
    if (!blk._startAt) {
      blk._startAt = now
    }
    blk._isThinking = true
    const totalMs = (blk._elapsedMs || 0) + (now - blk._startAt)
    blk.duration_s = Math.max(1, Math.round(totalMs / 1000))
    blk.text += chunkText
    target.reasoning_content += chunkText
    scrollToEnd()
  }

  const handleStepEvent = (target, stepData) => {
    pauseActiveReasoning(target)
    target._activeReasoningBlock = null
    target.steps.push(stepData)

    const ts = stepData.ts_ms || Date.now()
    if (stepData.action === 'call_tools') {
      target._toolRoundSeq = (target._toolRoundSeq || 0) + 1
      const rawCalls =
        stepData.calls ||
        (stepData.tools || []).map((t, i) => ({
          id: `call_${stepData.step || 0}_${i}`,
          name: t,
          summary: '',
        }))
      const calls = rawCalls.map((c, i) => ({
        id: c.id || `call_${target._toolRoundSeq}_${i}`,
        name: c.name || 'unknown',
        summary: c.summary || '',
        status: 'running',
      }))
      const toolBlock = {
        type: 'tool_round',
        round: target._toolRoundSeq,
        step: stepData.step || target._toolRoundSeq,
        start_ts_ms: ts,
        duration_ms: null,
        calls,
        _open: true,
      }
      target.blocks.push(toolBlock)
      target._activeToolRoundBlock = toolBlock
    } else if (stepData.action === 'tool_results') {
      let toolBlock = target._activeToolRoundBlock
      if (!toolBlock) {
        for (let idx = target.blocks.length - 1; idx >= 0; idx--) {
          if (target.blocks[idx].type === 'tool_round') {
            toolBlock = target.blocks[idx]
            break
          }
        }
      }
      if (toolBlock) {
        toolBlock.duration_ms = Math.max(0, ts - (toolBlock.start_ts_ms || ts))
        for (const res of stepData.results || []) {
          let hit = res.id ? toolBlock.calls.find((c) => c.id === res.id) : null
          if (!hit) {
            hit = toolBlock.calls.find((c) => c.name === res.tool && c.status === 'running')
          }
          if (hit) {
            hit.status = res.status || 'success'
          }
        }
      }
    }
    scrollToEnd()
  }

  const appendContent = (target, contentText) => {
    if (!contentText) return
    pauseActiveReasoning(target)
    target._activeReasoningBlock = null
    target._activeToolRoundBlock = null
    target.content += contentText

    const lastBlock = target.blocks[target.blocks.length - 1]
    if (!lastBlock || lastBlock.type !== 'content') {
      target.blocks.push({
        type: 'content',
        text: contentText,
      })
    } else {
      lastBlock.text += contentText
    }
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
            handleStepEvent(curMsg, data)
          } else if (event === 'delta') {
            if (data.reasoning_content) {
              appendReasoning(curMsg, data.reasoning_content)
            }
            if (data.thinking_done) {
              pauseActiveReasoning(curMsg)
            }
            if (data.content) {
              appendContent(curMsg, data.content)
            }
          } else if (event === 'error' && data.message) {
            finalizeMessageStream(curMsg, { cancelled: true })
            curMsg.status = 'failed'
            curMsg.error_message = data.message
            error.value = data.message
          } else if (event === 'done') {
            finalizeMessageStream(curMsg, { cancelled: false })
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
        finalizeMessageStream(target, { cancelled: true })
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
            <!-- 用户纯文本 -->
            <div v-if="m.role === 'user'" class="user-text">{{ m.content }}</div>

            <!-- AI 按轮次交错块流（思考块 / 工具轮次块 / 正文块） -->
            <template v-else>
              <template v-for="(block, bi) in m.blocks || []" :key="bi">
                <!-- 1. 单轮深度思考块 -->
                <div v-if="block.type === 'reasoning'" class="reasoning-card">
                  <button
                    type="button"
                    class="reasoning-header"
                    @click="toggleReasoningBlock(block)"
                  >
                    <el-icon v-if="block._isThinking" class="is-loading reasoning-icon active">
                      <Loading />
                    </el-icon>
                    <el-icon v-else class="reasoning-icon"><Opportunity /></el-icon>
                    <span>{{ reasoningBlockLabel(block) }}</span>
                    <el-icon :class="['reasoning-arrow', { expanded: block._open }]">
                      <ArrowRight />
                    </el-icon>
                  </button>
                  <div v-show="block._open" class="reasoning-body">
                    {{ block.text }}
                  </div>
                </div>

                <!-- 2. 单轮工具调用与原地状态流转块 -->
                <div v-else-if="block.type === 'tool_round'" class="tool-round-card">
                  <button
                    type="button"
                    class="tool-round-header"
                    @click="toggleToolRoundBlock(block)"
                  >
                    <el-icon v-if="isToolRoundRunning(block)" class="is-loading step-icon">
                      <Loading />
                    </el-icon>
                    <el-icon v-else class="step-icon done"><CircleCheck /></el-icon>
                    <span class="tool-round-title">
                      {{
                        isToolRoundRunning(block)
                          ? `第 ${block.round} 轮工具调用中（${block.calls?.length || 0} 项）…`
                          : `第 ${block.round} 轮工具调用（${block.calls?.length || 0} 项）`
                      }}
                    </span>
                    <span v-if="formatDurationMs(block.duration_ms)" class="tool-round-duration">
                      {{ formatDurationMs(block.duration_ms) }}
                    </span>
                    <el-icon :class="['reasoning-arrow', { expanded: block._open }]">
                      <ArrowRight />
                    </el-icon>
                  </button>
                  <div v-show="block._open" class="tool-calls-list">
                    <div v-for="(call, ci) in block.calls || []" :key="call.id || ci" class="tool-call-row">
                      <span class="tool-name-badge">
                        <el-icon class="tool-badge-icon"><Tools /></el-icon>
                        <span>{{ call.name }}</span>
                      </span>
                      <span v-if="call.summary" class="tool-arg-summary" :title="call.summary">
                        {{ call.summary }}
                      </span>
                      <el-tag
                        size="small"
                        :type="toolStatusTagType(call.status)"
                        effect="light"
                        class="tool-status-tag"
                      >
                        <el-icon v-if="call.status === 'running'" class="is-loading" style="margin-right: 3px">
                          <Loading />
                        </el-icon>
                        {{ toolStatusLabel(call.status) }}
                      </el-tag>
                    </div>
                  </div>
                </div>

                <!-- 3. Markdown 正文块 -->
                <MarkdownView
                  v-else-if="block.type === 'content'"
                  :content="block.text"
                  class="content-block"
                />
              </template>

              <!-- 初始等待或中断兜底显示 -->
              <div
                v-if="(!m.blocks || m.blocks.length === 0) && busy && i === messages.length - 1"
                class="thinking"
              >
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>正在思考与检索信息…</span>
              </div>
              <MarkdownView
                v-else-if="!hasContentBlock(m) && (!busy || i !== messages.length - 1) && fallbackText(m)"
                :content="fallbackText(m)"
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

/* 单轮工具调用与原地状态流转卡片 */
.tool-round-card {
  margin-bottom: 10px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  overflow: hidden;
  font-size: 12.5px;
  color: #475569;
}

.tool-round-header {
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

.tool-round-header:hover {
  background: #f8fafc;
}

.tool-round-title {
  color: #334155;
}

.tool-round-duration {
  margin-left: auto;
  margin-right: 4px;
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
  color: #94a3b8;
}

.tool-round-duration + .reasoning-arrow {
  margin-left: 0;
}

.step-icon {
  font-size: 14px;
  color: var(--el-color-primary);
}

.step-icon.done {
  color: var(--el-color-success);
}

.tool-calls-list {
  padding: 6px 11px 9px;
  border-top: 1px dashed #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-call-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.tool-name-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 5px;
  background: #f1f5f9;
  color: #1e293b;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  font-weight: 500;
}

.tool-badge-icon {
  font-size: 12px;
  color: #3b82f6;
}

.tool-arg-summary {
  color: #64748b;
  font-size: 12px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-status-tag {
  margin-left: auto;
}

.content-block + .reasoning-card,
.content-block + .tool-round-card {
  margin-top: 10px;
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
