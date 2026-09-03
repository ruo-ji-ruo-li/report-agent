// src/composables/useTaskPolling.ts —— 轮询状态机(spec-f §5.2)
import type { TaskInfo } from '../api/types'

export interface PollCallbacks {
  onUpdate: (info: TaskInfo) => void
  onAwaitingMeta: (info: TaskInfo) => void
  onDone: (info: TaskInfo) => void
  onFailed: (info: TaskInfo) => void
}

export function createTaskPoller(
  fetchTask: (id: string) => Promise<TaskInfo>,
  opts: { baseMs?: number; maxMs?: number } = {},
) {
  const baseMs = opts.baseMs ?? 2000
  const maxMs = opts.maxMs ?? 8000
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false
  let epoch = 0   // 代际计数:stop/start 使在途 tick 失效

  function stop() {
    stopped = true
    epoch += 1
    if (timer) clearTimeout(timer)
    timer = null
  }

  function start(taskId: string, cb: PollCallbacks) {
    stop()
    stopped = false
    const gen = epoch   // start 后 epoch 不再变,直到下次 stop/start
    let attempt = 0
    const tick = async () => {
      if (stopped || gen !== epoch) return
      let info: TaskInfo
      try {
        info = await fetchTask(taskId)
      } catch {
        // 网络抖动:重试同延迟,不冒泡(spec-f §11 降级不崩溃)
        if (stopped || gen !== epoch) return
        schedule()
        return
      }
      if (stopped || gen !== epoch) return  // 在途 fetch 返回后的复查
      switch (info.status) {
        case 'pending':
        case 'running':
          cb.onUpdate(info)
          schedule()
          break
        case 'awaiting_meta':
          cb.onAwaitingMeta(info)
          stopped = true
          break
        case 'completed':
        case 'degraded':
          cb.onDone(info)
          stopped = true
          break
        case 'failed':
          cb.onFailed(info)
          stopped = true
          break
      }
    }
    const schedule = () => {
      if (stopped || gen !== epoch) return
      const delay = Math.min(baseMs * 2 ** attempt, maxMs)
      attempt += 1
      timer = setTimeout(tick, delay)
    }
    void tick()
  }

  return { start, stop }
}
