// tests/TaskProgress.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskProgress from '../src/components/TaskProgress.vue'
import type { TaskInfo } from '../src/api/types'

describe('TaskProgress(spec-f §5.1/§10)', () => {
  it('当前阶段段挂 pulse 类(动画挂载点 = base.css reduced-motion 规则的生效对象)', () => {
    const info: TaskInfo = {
      task_id: 't1', report_id: 'r1', status: 'running', stage: 'generate',
      timings: { parse: 1.2, normalize: 0.8, compare: 0.5, retrieve: 0.6 },
      completed_stages: ['parse', 'normalize', 'compare', 'retrieve'],
      error: null,
    }
    const w = mount(TaskProgress, { props: { info } })
    const segs = w.findAll('.stage-seg')
    expect(segs).toHaveLength(7)
    expect(segs[4].classes()).toContain('pulse')      // generate 为当前段 → 有 pulse 类
    expect(segs[0].classes()).toContain('done')       // 已完成段有 done 类
    expect(segs[0].classes()).not.toContain('pulse')  // 非当前段无动画类
    expect(w.text()).toContain('生成解读')
  })
})
