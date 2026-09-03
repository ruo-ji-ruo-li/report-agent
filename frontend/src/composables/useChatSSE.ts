// src/composables/useChatSSE.ts —— SSE 追问(spec-f §5.3)
import { ref } from 'vue'
import type { ChatMessageRow } from '../api/types'
import { streamSSE } from '../api/sseParser'
import { applyChatEvent, startUserTurn, turnsFromHistory } from '../utils/chatState'
import type { ChatTurn } from '../utils/chatState'

export function useChatSSE(
  fetchHistory: (sessionId: string) => Promise<ChatMessageRow[]>,
) {
  const turns = ref<ChatTurn[]>([])
  const sending = ref(false)
  const errorMsg = ref<string | null>(null)

  function init(rows: ChatMessageRow[]) {
    turns.value = turnsFromHistory(rows)
  }

  async function send(sessionId: string, content: string) {
    if (sending.value) return // 重入守卫:在途发送中忽略新请求(composables 层约定固化)
    sending.value = true
    errorMsg.value = null
    turns.value = [...turns.value, ...startUserTurn(content)]
    const ac = new AbortController()
    try {
      await streamSSE(
        `/api/chat/sessions/${sessionId}/messages`,
        { content },
        ev => { turns.value = applyChatEvent(turns.value, ev) },
        ac.signal,
      )
      // error 事件(服务端生成失败,未持久化回答)保留本地 error 态,用户重发;不覆盖
    } catch {
      // 传输层断连:后端流会跑完并持久化完整回答 → history 覆盖本地(spec-f §5.3)
      await syncHistory(sessionId)
    } finally {
      sending.value = false
    }
  }

  async function syncHistory(sessionId: string) {
    try {
      const rows = await fetchHistory(sessionId)
      const last = turns.value[turns.value.length - 1]
      // 仅当本地末条仍是 streaming(未收到 done)时覆盖;否则保留已完成的流
      if (last && last.state === 'streaming') {
        turns.value = turnsFromHistory(rows)
      }
    } catch {
      turns.value = turns.value.map(t =>
        t.state === 'streaming' ? { ...t, state: 'error' as const } : t,
      )
      errorMsg.value = '生成失败,请重试'
    }
  }

  return { turns, sending, errorMsg, init, send }
}
