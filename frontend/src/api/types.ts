// src/api/types.ts
export type TaskStatus = 'pending' | 'awaiting_meta' | 'running' | 'completed' | 'degraded' | 'failed'
export type ItemStatus = 'high' | 'low' | 'critical_high' | 'critical_low' | 'normal' | 'unknown' | 'unmapped'
export type AdviceLevel = 'lifestyle' | 'recheck' | 'specialist' | 'urgent'
export type Verdict = 'pass' | 'suspect' | 'block'

export interface ManualItem {
  name: string
  value_text?: string | null
  value_num?: number | null
  unit?: string | null
  ref_range_text?: string | null
  abnormal_flag?: string | null
}
export interface ManualEntry {
  meta?: Record<string, unknown>
  items: ManualItem[]
}

export interface ReportMeta {
  id: string
  source: 'pdf' | 'photo' | 'manual'
  name: string | null
  institution: string | null
  report_date: string | null
  sex: string | null
  age: number | null
}
export interface RawItem {
  section: string | null
  name: string
  value_text: string | null
  value_num: number | null
  unit: string | null
  ref_range_text: string | null
  abnormal_flag: string | null
}
export interface NormalizedItem {
  item_name: string
  indicator_code: string | null
  value_num: number | null
  unit: string | null
  status: ItemStatus
  ref_low: number | null
  ref_high: number | null
  critical: boolean
  section: string | null
}
export interface TaskSummary { id: string; status: TaskStatus; stage: string | null; error: string | null }
export interface ReportDetail {
  meta: ReportMeta
  items: RawItem[]
  normalized: NormalizedItem[]
  task: TaskSummary | null
}
export interface TaskInfo {
  task_id: string
  report_id: string
  status: TaskStatus
  stage: string | null
  timings: Record<string, number> | null
  completed_stages: string[]
  error: string | null
}

export interface InterpretationItem {
  indicator_code: string | null
  name: string
  status: ItemStatus
  value_text: string
  meaning: string
  risks: string[]
  advice_level: AdviceLevel
  advice: string
  evidence_ids: string[]
}
export interface InterpretationDoc {
  summary: string
  items: InterpretationItem[]
  advice_summary: string
  disclaimer: string
  degraded: boolean
}
export interface FollowupItem { item: string; timeframe: string; department: string; basis: string }
export interface FollowupPlanDoc { items: FollowupItem[]; degraded: boolean }

export interface ChatMessageRow {
  role: 'user' | 'assistant'
  content: string
  guardrail_flags: string[] | null
  created_at: string | null
}

/** SSE 聊天事件(spec-f §4.3;token 为原文,其余为已解析 JSON) */
export type SSEChatEvent =
  | { event: 'token'; data: string }
  | { event: 'tool_call'; data: { name: string; status: 'start' | 'end' } }
  | { event: 'evidence'; data: string }
  | { event: 'safety'; data: string }
  | { event: 'error'; data: string }
  | { event: 'done'; data: { session_id: string; guardrail: Verdict } }
