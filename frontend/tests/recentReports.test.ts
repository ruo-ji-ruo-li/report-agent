// tests/recentReports.test.ts
import { describe, expect, it } from 'vitest'
import { loadRecent, upsertRecent, removeRecent } from '../src/utils/recentReports'
import type { RecentReport } from '../src/utils/recentReports'

function fakeStorage(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  }
}

const r1: RecentReport = { id: 'a', created_at: '2026-09-01T00:00:00Z', source: 'pdf', institution: null, report_date: null, status: 'completed' }

describe('recentReports(spec-f §5.4)', () => {
  it('空存储返回空列表', () => {
    expect(loadRecent(fakeStorage())).toEqual([])
  })

  it('写入后去重前插', () => {
    const s = fakeStorage()
    upsertRecent(s, r1)
    upsertRecent(s, { ...r1, status: 'failed' })
    upsertRecent(s, { ...r1, id: 'b' })
    const list = loadRecent(s)
    expect(list.map(x => x.id)).toEqual(['b', 'a'])
    expect(list[1].status).toBe('failed')
  })

  it('超出 20 条截断', () => {
    const s = fakeStorage()
    for (let i = 0; i < 25; i++) upsertRecent(s, { ...r1, id: `r${i}` })
    expect(loadRecent(s)).toHaveLength(20)
    expect(loadRecent(s)[0].id).toBe('r24')
  })

  it('删除仅移除一条', () => {
    const s = fakeStorage()
    upsertRecent(s, r1)
    upsertRecent(s, { ...r1, id: 'b' })
    const list = removeRecent(s, 'a')
    expect(list.map(x => x.id)).toEqual(['b'])
  })

  it('坏 JSON 返回空列表且不抛', () => {
    expect(loadRecent(fakeStorage({ 'report-agent:recent': '{{{oops' }))).toEqual([])
  })
})
