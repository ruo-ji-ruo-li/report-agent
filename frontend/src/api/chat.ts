// src/api/chat.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { ChatMessageRow } from './types'

export async function createChatSession(reportId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.post<{ session_id: string }>(`/reports/${reportId}/chat/sessions`)
  return data
}

export async function getChatHistory(sessionId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ messages: ChatMessageRow[] }>(`/chat/sessions/${sessionId}/history`)
  return data.messages
}
