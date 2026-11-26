const TOKEN_KEY = 'trippilot_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function api(path, body) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: body !== undefined ? 'POST' : 'GET',
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

export async function apiStream(path, body, { onEvent, signal } = {}) {
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

  while (true) {
    const { done, value } = await reader.read()
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
}
