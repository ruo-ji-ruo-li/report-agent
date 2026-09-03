// src/utils/chatState.ts —— 聊天视图状态机(纯函数,spec-f §5.3/§4.3)
import type { ChatMessageRow, SSEChatEvent, Verdict } from '../api/types'

export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  state: 'streaming' | 'done' | 'error' | 'safety'
  tool: { name: string; label: string } | null
  evidence: string[]
  guardrail: Verdict | null
}

const TOOL_LABELS: Record<string, string> = {
  get_my_report: '正在查看你的报告',
  query_indicator_knowledge: '正在查询指标知识',
  compute_reference_range: '正在核对参考区间',
  search_knowledge: '正在检索医学知识库',
}

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? '正在查询…'
}

function newTurn(role: 'user' | 'assistant', text: string, state: ChatTurn['state']): ChatTurn {
  return { role, text, state, tool: null, evidence: [], guardrail: null }
}

export function startUserTurn(text: string): ChatTurn[] {
  return [newTurn('user', text, 'done'), newTurn('assistant', '', 'streaming')]
}

/** 事件应用到最后一条 assistant turn;不可变更新。 */
export function applyChatEvent(turns: ChatTurn[], ev: SSEChatEvent): ChatTurn[] {
  const idx = turns.length - 1
  const last = turns[idx]
  if (!last || last.role !== 'assistant') return turns
  let next: ChatTurn
  switch (ev.event) {
    case 'token':
      next = { ...last, text: last.text + ev.data }
      break
    case 'tool_call':
      next = ev.data.status === 'start'
        ? { ...last, tool: { name: ev.data.name, label: toolLabel(ev.data.name) } }
        : { ...last, tool: null }
      break
    case 'evidence':
      next = { ...last, evidence: [...last.evidence, ev.data] }
      break
    case 'safety':
      next = { ...last, text: ev.data, state: 'safety', evidence: [] }
      break
    case 'error':
      next = { ...last, state: 'error' }
      break
    case 'done':
      // BLOCK 场景后端先发 safety 事件、随后同帧 done(guardrail:'block'):末条已是
      // safety 时保留该态只落 guardrail,否则实时流中的"经安全校验"标记被 done 覆盖,
      // 要刷新后经 turnsFromHistory 才重现,前后行为不一致(spec-f §4.3)
      next = last.state === 'safety'
        ? { ...last, guardrail: ev.data.guardrail }
        : { ...last, state: 'done', guardrail: ev.data.guardrail }
      break
  }
  return [...turns.slice(0, idx), next]
}

export function turnsFromHistory(rows: ChatMessageRow[]): ChatTurn[] {
  return rows.map(r => {
    const t = newTurn(r.role, r.content, 'done')
    if (r.role === 'assistant' && r.guardrail_flags && r.guardrail_flags.length > 0) {
      t.state = 'safety'
    }
    return t
  })
}
