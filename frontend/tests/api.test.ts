import { describe, expect, it, vi } from 'vitest'
import type { AxiosInstance } from 'axios'
import { createReportFromFile, createReportManual, getReport, patchReportMeta } from '../src/api/reports'
import { getTask } from '../src/api/tasks'
import { createChatSession, getChatHistory } from '../src/api/chat'

function mockClient(data: unknown) {
  const post = vi.fn().mockResolvedValue({ data })
  const get = vi.fn().mockResolvedValue({ data })
  const patch = vi.fn().mockResolvedValue({ data })
  return { post, get, patch } as unknown as AxiosInstance
}

describe('api 模块', () => {
  it('createReportFromFile 以 multipart 上传并返回 report_id/task_id', async () => {
    const c = mockClient({ report_id: 'r1', task_id: 't1' })
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' })
    const res = await createReportFromFile(file, c)
    expect(c.post).toHaveBeenCalledWith('/reports', expect.any(FormData))
    const fd = (c.post as ReturnType<typeof vi.fn>).mock.calls[0][1] as FormData
    expect(fd.get('file')).toBe(file)
    expect(res).toEqual({ report_id: 'r1', task_id: 't1' })
  })

  it('createReportManual 以 JSON 提交录入', async () => {
    const c = mockClient({ report_id: 'r2', task_id: 't2' })
    const entry = { meta: { sex: '男', age: 35 }, items: [{ name: 'ALT' }] }
    const res = await createReportManual(entry, c)
    expect(c.post).toHaveBeenCalledWith('/reports', entry)
    expect(res.task_id).toBe('t2')
  })

  it('getReport / getTask / patchReportMeta / 会话与历史 URL 正确', async () => {
    const c = mockClient({})
    await getReport('r1', c); expect(c.get).toHaveBeenCalledWith('/reports/r1')
    await getTask('t1', c); expect(c.get).toHaveBeenCalledWith('/tasks/t1')
    await patchReportMeta('r1', { sex: '女' }, c)
    expect(c.patch).toHaveBeenCalledWith('/reports/r1/meta', { sex: '女' })
    await createChatSession('r1', c); expect(c.post).toHaveBeenCalledWith('/reports/r1/chat/sessions')
    await getChatHistory('s1', c); expect(c.get).toHaveBeenCalledWith('/chat/sessions/s1/history')
  })
})
