// src/api/health.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'

export async function getHealth(client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ status: string; checks: Record<string, boolean> }>('/health')
  return data
}
