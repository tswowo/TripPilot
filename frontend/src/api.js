const TOKEN_KEY = 'trippilot_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function api(path, body, method) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: method ?? (body !== undefined ? 'POST' : 'GET'),
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401) clearToken()
    const detail = typeof data.detail === 'string' ? data.detail : `请求失败（${res.status}）`
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return data
}

function makeAbortError() {
  return new DOMException('The operation was aborted.', 'AbortError')
}

export async function apiStream(path, body, { onEvent, signal } = {}) {
  if (signal?.aborted) throw makeAbortError()

  const headers = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok) {
    if (res.status === 401) clearToken()
    const data = await res.json().catch(() => ({}))
    const detail = typeof data.detail === 'string' ? data.detail : `请求失败（${res.status}）`
    const err = new Error(detail)
    err.status = res.status
    throw err
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // fetch 响应头返回后，模型思考或跑工具期间流上可能数秒无字节；
  // 显式监听 abort 并主动 reader.cancel()，确保点击“停止”瞬间解除 reader.read() 阻塞
  const onAbort = () => {
    reader.cancel().catch(() => {})
  }
  if (signal) {
    if (signal.aborted) {
      onAbort()
      throw makeAbortError()
    }
    signal.addEventListener('abort', onAbort, { once: true })
  }

  const processBlock = (block) => {
    const lines = block.split('\n')
    let event = 'message'
    const dataLines = []
    for (const rawLine of lines) {
      const line = rawLine.replace(/\r$/, '')
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }
    if (dataLines.length > 0 && onEvent) {
      try {
        const parsed = JSON.parse(dataLines.join('\n'))
        onEvent(event, parsed)
      } catch {
        // 忽略非 JSON 心跳或注释行
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (signal?.aborted) throw makeAbortError()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() ?? ''
      for (const block of parts) {
        if (block.trim()) processBlock(block)
      }
    }
    if (buffer.trim()) {
      processBlock(buffer)
    }
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}
