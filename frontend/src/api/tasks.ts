// src/api/tasks.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { TaskInfo } from './types'

export async function getTask(taskId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<TaskInfo>(`/tasks/${taskId}`)
  return data
}
