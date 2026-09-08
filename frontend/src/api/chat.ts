// src/api/chat.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { ChatMessageRow } from './types'

/** 追问会话列表项(GET /reports/{id}/chat/sessions):preview 为首条提问截断,作会话标题 */
export interface ChatSessionRow {
  session_id: string
  created_at: string | null
  message_count: number
  preview: string
}

export async function createChatSession(reportId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.post<{ session_id: string }>(`/reports/${reportId}/chat/sessions`)
  return data
}

export async function listChatSessions(reportId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ sessions: ChatSessionRow[] }>(`/reports/${reportId}/chat/sessions`)
  return data.sessions
}

export async function getChatHistory(sessionId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ messages: ChatMessageRow[] }>(`/chat/sessions/${sessionId}/history`)
  return data.messages
}
