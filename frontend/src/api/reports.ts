// src/api/reports.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { FollowupPlanDoc, InterpretationDoc, ManualEntry, ReportDetail } from './types'

export async function createReportFromFile(file: File, client: AxiosInstance = apiClient) {
  const fd = new FormData()
  fd.append('file', file)
  const { data } = await client.post<{ report_id: string; task_id: string }>('/reports', fd)
  return data
}

export async function createReportManual(entry: ManualEntry, client: AxiosInstance = apiClient) {
  const { data } = await client.post<{ report_id: string; task_id: string }>('/reports', entry)
  return data
}

export async function getReport(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<ReportDetail>(`/reports/${id}`)
  return data
}

export async function patchReportMeta(id: string, patch: { sex?: string; age?: number }, client: AxiosInstance = apiClient) {
  await client.patch(`/reports/${id}/meta`, patch)
}

export async function getInterpretation(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<InterpretationDoc>(`/reports/${id}/interpretation`)
  return data
}

export async function getFollowupPlan(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<FollowupPlanDoc>(`/reports/${id}/followup-plan`)
  return data
}
