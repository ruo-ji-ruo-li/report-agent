// tests/chatState.test.ts
import { describe, expect, it } from 'vitest'
import { applyChatEvent, startUserTurn, toolLabel, turnsFromHistory } from '../src/utils/chatState'

describe('startUserTurn / applyChatEvent', () => {
  it('提问追加 user + 空 assistant', () => {
    const turns = startUserTurn('什么是 ALT?')
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ role: 'user', text: '什么是 ALT?', state: 'done' })
    expect(turns[1]).toMatchObject({ role: 'assistant', text: '', state: 'streaming' })
  })

  it('token 拼接;tool_call 起止设置/清除状态行;evidence 收集', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'token', data: '你' })
    t = applyChatEvent(t, { event: 'token', data: '好' })
    t = applyChatEvent(t, { event: 'tool_call', data: { name: 'search_knowledge', status: 'start' } })
    expect(t[1].text).toBe('你好')
    expect(t[1].tool).toEqual({ name: 'search_knowledge', label: '正在检索医学知识库' })
    t = applyChatEvent(t, { event: 'evidence', data: '[e0] 证据文本' })
    t = applyChatEvent(t, { event: 'tool_call', data: { name: 'search_knowledge', status: 'end' } })
    expect(t[1].evidence).toEqual(['[e0] 证据文本'])
    expect(t[1].tool).toBeNull()
  })

  it('safety 事件整条替换并标记', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'token', data: '被丢弃的内容' })
    t = applyChatEvent(t, { event: 'safety', data: '本条回答未通过内容安全校验。' })
    expect(t[1]).toMatchObject({ text: '本条回答未通过内容安全校验。', state: 'safety' })
  })

  it('error 事件标记错误态;done 记录 guardrail', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'error', data: '生成失败' })
    expect(t[1].state).toBe('error')
    t = startUserTurn('y')
    t = applyChatEvent(t, { event: 'done', data: { session_id: 's1', guardrail: 'block' } })
    expect(t[1].state).toBe('done')
    expect(t[1].guardrail).toBe('block')
  })

  it('不可变更新:原数组不被修改', () => {
    const t = startUserTurn('x')
    applyChatEvent(t, { event: 'token', data: 'a' })
    expect(t[1].text).toBe('')
  })
})

describe('toolLabel', () => {
  it('四个工具名映射文案,未知名用通用文案', () => {
    expect(toolLabel('get_my_report')).toBe('正在查看你的报告')
    expect(toolLabel('query_indicator_knowledge')).toBe('正在查询指标知识')
    expect(toolLabel('compute_reference_range')).toBe('正在核对参考区间')
    expect(toolLabel('search_knowledge')).toBe('正在检索医学知识库')
    expect(toolLabel('unknown_tool')).toBe('正在查询…')
  })
})

describe('turnsFromHistory', () => {
  it('历史恢复:guardrail_flags 非空标 safety', () => {
    const turns = turnsFromHistory([
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '回答', guardrail_flags: ['diagnosis_term'], created_at: null },
    ])
    expect(turns[0]).toMatchObject({ role: 'user', state: 'done' })
    expect(turns[1]).toMatchObject({ role: 'assistant', text: '回答', state: 'safety' })
  })
})
