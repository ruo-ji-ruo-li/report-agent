// tests/views.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import HomeView from '../src/views/HomeView.vue'
import ReportView from '../src/views/ReportView.vue'
import * as reportsApi from '../src/api/reports'
import * as tasksApi from '../src/api/tasks'

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
      meta: { id: 'r1', source: 'pdf', institution: '平安健康体检中心', report_date: '2026-08-28', sex: '男', age: 35 },
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
      meta: { id: 'r1', source: 'pdf', institution: null, report_date: null, sex: null, age: null },
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
})
