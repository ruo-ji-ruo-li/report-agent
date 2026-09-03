// tests/useTaskPolling.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createTaskPoller } from '../src/composables/useTaskPolling'
import type { TaskInfo } from '../src/api/types'

function info(status: TaskInfo['status']): TaskInfo {
  return { task_id: 't1', report_id: 'r1', status, stage: 'parse',
           timings: {}, completed_stages: [], error: null }
}

describe('createTaskPoller(spec-f §5.2)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('立即首查,运行态按指数退避继续', async () => {
    const fetchTask = vi.fn()
      .mockResolvedValueOnce(info('running'))
      .mockResolvedValueOnce(info('running'))
      .mockResolvedValueOnce(info('completed'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask, { baseMs: 2000, maxMs: 8000 })
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchTask).toHaveBeenCalledTimes(1)
    expect(cb.onUpdate).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000) // 第二次(2s)
    await vi.advanceTimersByTimeAsync(4000) // 第三次(4s 退避)
    expect(fetchTask).toHaveBeenCalledTimes(3)
    expect(cb.onDone).toHaveBeenCalledTimes(1)
    // 终态后停止
    await vi.advanceTimersByTimeAsync(8000)
    expect(fetchTask).toHaveBeenCalledTimes(3)
  })

  it('awaiting_meta 触发回调并停止', async () => {
    const fetchTask = vi.fn().mockResolvedValue(info('awaiting_meta'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask)
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onAwaitingMeta).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(8000)
    expect(fetchTask).toHaveBeenCalledTimes(1)
  })

  it('failed 触发 onFailed;stop 立即停止;可重新 start', async () => {
    const fetchTask = vi.fn().mockResolvedValue(info('failed'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask)
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onFailed).toHaveBeenCalledTimes(1)

    fetchTask.mockResolvedValue(info('completed'))
    p.start('t1', cb) // PATCH 后恢复
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onDone).toHaveBeenCalledTimes(1)
  })
})
