// src/api/sseParser.ts —— spec-f §4.3/§8.3
import type { SSEChatEvent } from './types'

export interface RawSSEEvent { event: string; data: string }

const JSON_EVENTS = new Set(['tool_call', 'evidence', 'safety', 'error', 'done'])

/** 按 SSE 帧边界缓冲文本块,输出完整事件。token 的 data 保持原文。 */
export class SSEFrameParser {
  private buf = ''

  push(chunk: string): RawSSEEvent[] {
    this.buf += chunk
    const out: RawSSEEvent[] = []
    let idx: number
    while ((idx = this.buf.indexOf('\n\n')) !== -1) {
      const frame = this.buf.slice(0, idx)
      this.buf = this.buf.slice(idx + 2)
      const ev = this.parseFrame(frame)
      if (ev) out.push(ev)
    }
    return out
  }

  private parseFrame(frame: string): RawSSEEvent | null {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith(':')) continue // 注释(ping)
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (dataLines.length === 0) return null
    return { event, data: dataLines.join('\n') }
  }
}

/** 帧 → 聊天事件:token 原文透传(后端注释明示不可 JSON.parse),其余 JSON.parse。 */
export function decodeChatEvent(raw: RawSSEEvent): SSEChatEvent | null {
  if (raw.event === 'token') return { event: 'token', data: raw.data }
  if (!JSON_EVENTS.has(raw.event)) return null
  try {
    return { event: raw.event, data: JSON.parse(raw.data) } as SSEChatEvent
  } catch {
    return null
  }
}

/** fetch 流式 POST,逐事件回调。调用方以 done/error 事件或异常为终态。 */
export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (e: SSEChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SSEFrameParser()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
      const ev = decodeChatEvent(raw)
      if (ev) onEvent(ev)
    }
  }
}
