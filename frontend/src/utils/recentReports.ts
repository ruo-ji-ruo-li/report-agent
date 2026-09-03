// src/utils/recentReports.ts —— 后端无列表端点,本地记录(spec-f §5.4)
import type { TaskStatus } from '../api/types'

export interface RecentReport {
  id: string
  created_at: string
  source: 'pdf' | 'photo' | 'manual'
  institution: string | null
  report_date: string | null
  status: TaskStatus | null
}

const KEY = 'report-agent:recent'
const MAX = 20

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

export function loadRecent(storage: StorageLike = localStorage): RecentReport[] {
  try {
    const parsed = JSON.parse(storage.getItem(KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function upsertRecent(storage: StorageLike, entry: RecentReport): RecentReport[] {
  const list = [entry, ...loadRecent(storage).filter(x => x.id !== entry.id)].slice(0, MAX)
  storage.setItem(KEY, JSON.stringify(list))
  return list
}

export function removeRecent(storage: StorageLike, id: string): RecentReport[] {
  const list = loadRecent(storage).filter(x => x.id !== id)
  storage.setItem(KEY, JSON.stringify(list))
  return list
}
