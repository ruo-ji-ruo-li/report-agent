// src/api/reports.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { FollowupPlanDoc, InterpretationDoc, ManualEntry, ReportDetail } from './types'

// 上传表单元数据(spec §11.1):sex 取值 male / female(前端下拉 value 即映射,不入中文)
export interface UploadMeta {
  name: string
  sex: string // male / female
  age: number
  institution?: string
  report_date?: string
}

export async function createReportFromFile(file: File, meta: UploadMeta, client: AxiosInstance = apiClient) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('name', meta.name)
  fd.append('sex', meta.sex)
  fd.append('age', String(meta.age))
  if (meta.institution) fd.append('institution', meta.institution)
  if (meta.report_date) fd.append('report_date', meta.report_date)
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
