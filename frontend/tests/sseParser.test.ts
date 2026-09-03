// tests/sseParser.test.ts
import { describe, expect, it } from 'vitest'
import { SSEFrameParser, decodeChatEvent } from '../src/api/sseParser'

describe('SSEFrameParser', () => {
  it('一帧拆在两个 chunk 中也能解析', () => {
    const p = new SSEFrameParser()
    const a = p.push('event: token\nda')
    const b = p.push('ta: 你好\n\n')
    expect(a).toEqual([])
    expect(b).toEqual([{ event: 'token', data: '你好' }])
  })

  it('同帧多 data 行以换行合并(证据文本含换行)', () => {
    const p = new SSEFrameParser()
    expect(p.push('event: evidence\ndata: 第一行\ndata: 第二行\n\n'))
      .toEqual([{ event: 'evidence', data: '第一行\n第二行' }])
  })

  it('忽略 ":" 注释行(ping)', () => {
    const p = new SSEFrameParser()
    expect(p.push(': ping\n\nevent: token\ndata: x\n\n'))
      .toEqual([{ event: 'token', data: 'x' }])
  })

  it('一帧可含多个事件', () => {
    const p = new SSEFrameParser()
    expect(p.push('event: tool_call\ndata: {"name":"a","status":"start"}\n\nevent: token\ndata: 好\n\n').length).toBe(2)
  })
})

describe('decodeChatEvent', () => {
  it('token 原文透传,不 JSON.parse', () => {
    expect(decodeChatEvent({ event: 'token', data: '我来' }))
      .toEqual({ event: 'token', data: '我来' })
  })

  it('结构化事件 JSON.parse', () => {
    expect(decodeChatEvent({ event: 'tool_call', data: '{"name":"search_knowledge","status":"start"}' }))
      .toEqual({ event: 'tool_call', data: { name: 'search_knowledge', status: 'start' } })
  })

  it('done 事件解析 guardrail', () => {
    expect(decodeChatEvent({ event: 'done', data: '{"session_id":"s1","guardrail":"pass"}' }))
      .toEqual({ event: 'done', data: { session_id: 's1', guardrail: 'pass' } })
  })

  it('坏 JSON 与未知事件返回 null', () => {
    expect(decodeChatEvent({ event: 'tool_call', data: 'not json' })).toBeNull()
    expect(decodeChatEvent({ event: 'mystery', data: '{}' })).toBeNull()
  })
})
