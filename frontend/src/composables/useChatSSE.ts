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

  function init(rows: ChatMessageRow[]) {
    turns.value = turnsFromHistory(rows)
  }

  async function send(sessionId: string, content: string) {
    if (sending.value) return // 重入守卫:在途发送中忽略新请求(composables 层约定固化)
    sending.value = true
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

  /** 把在途半截的 streaming 末条标为 error(气泡 error-note 提示重试,保留提问) */
  function markStreamingError() {
    turns.value = turns.value.map(t =>
      t.state === 'streaming' ? { ...t, state: 'error' as const } : t,
    )
  }

  async function syncHistory(sessionId: string) {
    try {
      const rows = await fetchHistory(sessionId)
      const last = turns.value[turns.value.length - 1]
      // 仅当本地末条仍是 streaming(未收到 done)时覆盖;否则保留已完成的流
      if (last && last.state === 'streaming') {
        // 本轮回答缺失判定(spec-f §5.3):rows 里从末往前找本地发送的 user 文本,
        // 找不到,或找到但无随后的 assistant 行 → 服务端流中途崩、回答未持久化:
        // 不替换本地视图,保留提问并把半截末条标 error;否则完整回答已入库 → 历史覆盖
        const asked = turns.value[turns.value.length - 2]
        let hit = -1
        if (asked && asked.role === 'user') {
          for (let i = rows.length - 1; i >= 0; i--) {
            if (rows[i].role === 'user' && rows[i].content === asked.text) { hit = i; break }
          }
        }
        const answered = hit !== -1 && hit + 1 < rows.length && rows[hit + 1].role === 'assistant'
        if (answered) {
          turns.value = turnsFromHistory(rows)
        } else {
          markStreamingError()
        }
      }
    } catch {
      markStreamingError() // 拉取本身失败:同样保留本地、末条标 error(文案由气泡 error-note 呈现)
    }
  }

  return { turns, sending, init, send }
}
