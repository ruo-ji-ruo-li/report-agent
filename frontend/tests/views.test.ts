// tests/views.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import HomeView from '../src/views/HomeView.vue'
import ReportView from '../src/views/ReportView.vue'
import * as reportsApi from '../src/api/reports'
import * as tasksApi from '../src/api/tasks'
import type { ReportDetail, TaskInfo } from '../src/api/types'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { id: 'r1' } }),
}))

const globalOpts = { global: { plugins: [ElementPlus] } }

describe('视图冒烟(spec-f §9)', () => {
  it('HomeView 渲染论点区/上传 tabs/最近报告空态', () => {
    const w = mount(HomeView, globalOpts)
    expect(w.text()).toContain('看懂你的体检报告')
    expect(w.text()).toContain('上传文件')
    expect(w.text()).toContain('手动录入')
    expect(w.text()).toContain('还没有报告,先上传第一份吧。')
  })

  it('ReportView 终态渲染封面带与解读四段', async () => {
    vi.spyOn(reportsApi, 'getReport').mockResolvedValue({
      meta: { id: 'r1', source: 'pdf', name: '张三', institution: '平安健康体检中心', report_date: '2026-08-28', sex: '男', age: 35 },
      items: [],
      normalized: [{ item_name: '总胆固醇', indicator_code: 'TC', value_num: 6.31, unit: 'mmol/L', status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null }],
      task: { id: 't1', status: 'completed', stage: null, error: null },
    })
    vi.spyOn(reportsApi, 'getInterpretation').mockResolvedValue({
      summary: '总体结论文本',
      items: [{ indicator_code: 'TC', name: '总胆固醇', status: 'high', value_text: '6.31 mmol/L(参考区间 2.8~5.2)', meaning: '高于参考区间', risks: [], advice_level: 'recheck', advice: '建议低脂饮食', evidence_ids: [] }],
      advice_summary: '【定期复查】总胆固醇: 建议低脂饮食',
      disclaimer: '【免责声明】本解读仅供健康参考。',
      degraded: false,
    })
    vi.spyOn(reportsApi, 'getFollowupPlan').mockResolvedValue({
      items: [{ item: '复查总胆固醇', timeframe: '3 个月后', department: '心内科', basis: '偏高' }],
      degraded: false,
    })
    const w = mount(ReportView, globalOpts)
    await flushPromises()
    expect(w.text()).toContain('平安健康体检中心')
    expect(w.text()).toContain('总体结论')
    expect(w.text()).toContain('高于参考区间')
  })

  it('ReportView awaiting_meta 显示补录表单', async () => {
    vi.spyOn(reportsApi, 'getReport').mockResolvedValue({
      meta: { id: 'r1', source: 'pdf', name: '张三', institution: null, report_date: null, sex: null, age: null },
      items: [], normalized: [],
      task: { id: 't1', status: 'awaiting_meta', stage: 'normalize', error: null },
    })
    vi.spyOn(tasksApi, 'getTask').mockResolvedValue({
      task_id: 't1', report_id: 'r1', status: 'awaiting_meta', stage: 'normalize', timings: {}, completed_stages: [], error: null,
    })
    const w = mount(ReportView, globalOpts)
    await flushPromises()
    expect(w.text()).toContain('保存并继续解读')
  })

  it('ReportView 详情加载失败渲染错误块而非白屏', async () => {
    vi.spyOn(reportsApi, 'getReport').mockRejectedValue({ response: { status: 404 } })
    const w = mount(ReportView, globalOpts)
    await flushPromises()
    expect(w.text()).toContain('报告不存在或服务暂不可用。')
    expect(w.text()).toContain('回首页')
  })

  it('ReportView 轮询竞态:迟到的旧 running 快照不覆写终态', async () => {
    vi.useFakeTimers()
    try {
      const running: ReportDetail = {
        meta: { id: 'r1', source: 'pdf', name: '张三', institution: '平安健康体检中心', report_date: '2026-08-28', sex: '男', age: 35 },
        items: [], normalized: [],
        task: { id: 't1', status: 'running', stage: 'generate', error: null },
      }
      const completed: ReportDetail = {
        meta: { id: 'r1', source: 'pdf', name: '张三', institution: '平安健康体检中心', report_date: '2026-08-28', sex: '男', age: 35 },
        items: [], normalized: [],
        task: { id: 't1', status: 'completed', stage: null, error: null },
      }
      let calls = 0
      let staleResolve!: (d: ReportDetail) => void
      const staleSnapshot = new Promise<ReportDetail>(res => { staleResolve = res })
      const getSpy = vi.spyOn(reportsApi, 'getReport').mockImplementation(() => {
        calls += 1
        if (calls === 1) return Promise.resolve(running)   // 挂载首查
        if (calls === 2) return staleSnapshot              // tick1 onUpdate 在途刷新(悬挂)
        return Promise.resolve(completed)                  // tick2 onDone 刷新
      })
      const taskResults: TaskInfo[] = [
        { task_id: 't1', report_id: 'r1', status: 'running', stage: 'generate', timings: {}, completed_stages: [], error: null },
        { task_id: 't1', report_id: 'r1', status: 'completed', stage: null, timings: {}, completed_stages: [], error: null },
      ]
      vi.spyOn(tasksApi, 'getTask').mockImplementation(() => Promise.resolve(taskResults.shift()!))
      vi.spyOn(reportsApi, 'getInterpretation').mockResolvedValue({
        summary: '总体结论文本', items: [], advice_summary: '', disclaimer: '', degraded: false,
      })
      vi.spyOn(reportsApi, 'getFollowupPlan').mockResolvedValue({ items: [], degraded: false })

      const w = mount(ReportView, globalOpts)
      await vi.advanceTimersByTimeAsync(0)    // 挂载 + tick1(running)已把第二次 getReport 挂起
      expect(getSpy).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(2000) // tick2:completed → onDone 快照 + 文档加载
      staleResolve(running)                   // 迟到的旧 running 快照此刻才返回
      await vi.advanceTimersByTimeAsync(0)
      expect(w.text()).toContain('总体结论文本') // 终态解读仍可见
      expect(w.find('.task-progress').exists()).toBe(false) // 无进度区残留
    } finally {
      vi.useRealTimers()
    }
  })
})
