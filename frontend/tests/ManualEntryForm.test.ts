// tests/ManualEntryForm.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import ManualEntryForm from '../src/components/ManualEntryForm.vue'
import * as api from '../src/api/reports'

const globalOpts = { global: { plugins: [ElementPlus] } }

describe('ManualEntryForm', () => {
  it('空提交显示校验提示且不请求', async () => {
    const spy = vi.spyOn(api, 'createReportManual')
    const w = mount(ManualEntryForm, globalOpts)
    await w.find('button[type="submit"]').trigger('click')
    await flushPromises()
    expect(spy).not.toHaveBeenCalled()
    expect(w.text()).toContain('至少填写一条检验项目')
  })

  it('填一条后提交成功并派发 uploaded', async () => {
    const spy = vi.spyOn(api, 'createReportManual').mockResolvedValue({ report_id: 'r9', task_id: 't9' })
    const w = mount(ManualEntryForm, globalOpts)
    const vm = w.vm as unknown as {
      rows: { name: string }[]; sex: string; age: number | null
      submit: () => Promise<void>
    }
    vm.rows[0].name = '丙氨酸氨基转移酶'
    vm.sex = 'male'
    vm.age = 35
    await vm.submit()
    await flushPromises()
    // 载荷归一契约:空名行被过滤、meta ''→null、sex 下拉值 male/female 随 ref 透传
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toEqual({
      meta: { sex: 'male', age: 35, institution: null, report_date: null },
      items: [{ name: '丙氨酸氨基转移酶' }],
    })
    expect(w.emitted('uploaded')![0]).toEqual([{ reportId: 'r9', taskId: 't9', source: 'manual' }])
  })
})
