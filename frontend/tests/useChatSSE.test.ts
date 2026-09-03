// tests/useChatSSE.test.ts
import { describe, expect, it, vi } from 'vitest'
import { useChatSSE } from '../src/composables/useChatSSE'
import type { ChatMessageRow } from '../src/api/types'
import { streamSSE } from '../src/api/sseParser'

vi.mock('../src/api/sseParser', () => ({
  streamSSE: vi.fn(),
}))

describe('useChatSSE(spec-f §5.3)', () => {
  it('send 时通过 streamSSE 流式发出,事件驱动 turns', async () => {
    const mockStream = vi.mocked(streamSSE).mockImplementation(async (_url, _body, onEvent) => {
      onEvent({ event: 'token', data: '你' })
      onEvent({ event: 'token', data: '好' })
      onEvent({ event: 'done', data: { session_id: 's1', guardrail: 'pass' } })
    })
    const history: ChatMessageRow[] = [
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '完整回答', guardrail_flags: null, created_at: null },
    ]
    const { turns, init, send } = useChatSSE(async () => history)
    init(history)
    await send('s1', '新提问')
    expect(mockStream).toHaveBeenCalledWith('/api/chat/sessions/s1/messages', { content: '新提问' }, expect.any(Function), expect.anything())
    expect(turns.value).toHaveLength(4) // 历史 2 + 新提问 user/assistant
    expect(turns.value[3].text).toBe('你好')
    expect(turns.value[3].state).toBe('done')
  })

  it('流异常后以 history 覆盖本地视图', async () => {
    vi.mocked(streamSSE).mockRejectedValue(new Error('network'))
    const history: ChatMessageRow[] = [
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '完整回答', guardrail_flags: null, created_at: null },
    ]
    const { turns, init, send } = useChatSSE(async () => history)
    init(history)
    await send('s1', '问题')
    // 覆盖同步:原 streaming 半截丢弃,历史完整回答呈现
    expect(turns.value.map(t => t.text)).toContain('完整回答')
    expect(turns.value.every(t => t.state === 'done')).toBe(true)
  })

  it('history 不可用时将 streaming 末条标记为 error', async () => {
    vi.mocked(streamSSE).mockRejectedValue(new Error('network'))
    const { turns, send } = useChatSSE(async () => { throw new Error('db down') })
    await send('s1', '问题')
    expect(turns.value[turns.value.length - 1].state).toBe('error')
  })
})
