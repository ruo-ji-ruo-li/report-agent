// tests/UploadZone.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import UploadZone from '../src/components/UploadZone.vue'
import * as api from '../src/api/reports'

type VM = { submit: () => Promise<void>; onFileChange: (f: { raw: File }) => void;
            form: { name: string; sex: string; age: number | null } }
const PDF = new File(['x'], 'a.pdf', { type: 'application/pdf' })
// EP 的 form-item validateState 经 refDebounced(100ms)才落到 DOM 错误文案(探针定位),
// 断言前需等过防抖窗口;其余用例不涉及错误文案,无需等待
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

function setup() {
  const w = mount(UploadZone, { global: { plugins: [ElementPlus] } })
  return { w, vm: w.vm as unknown as VM }
}

describe('UploadZone', () => {
  it('填写必填项并选择文件后,携带 meta 上传并派发事件', async () => {
    const spy = vi.spyOn(api, 'createReportFromFile').mockResolvedValue({ report_id: 'r1', task_id: 't1' })
    const { w, vm } = setup()
    vm.form.name = '张三'
    vm.form.sex = 'male'
    vm.form.age = 45
    vm.onFileChange({ raw: PDF })
    await vm.submit()
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(PDF, { name: '张三', sex: 'male', age: 45,
                                            institution: undefined, report_date: undefined })
    expect(w.emitted('uploaded')![0]).toEqual([{ reportId: 'r1', taskId: 't1', source: 'pdf' }])
  })

  it('name/sex/age 未填写时提交被拦截并显示校验错误', async () => {
    const spy = vi.spyOn(api, 'createReportFromFile').mockResolvedValue({ report_id: 'r1', task_id: 't1' })
    const { w, vm } = setup()
    vm.onFileChange({ raw: PDF })
    await vm.submit()
    await flushPromises()
    await sleep(150) // 等 EP 100ms 防抖后错误文案渲染完成
    expect(spy).not.toHaveBeenCalled() // 必填校验拦截
    expect(w.text()).toContain('请填写姓名')
    expect(w.text()).toContain('请选择性别')
    expect(w.text()).toContain('请填写年龄')
  })

  it('未选择文件时提示选择文件', async () => {
    const spy = vi.spyOn(api, 'createReportFromFile').mockResolvedValue({ report_id: 'r1', task_id: 't1' })
    const { w, vm } = setup()
    vm.form.name = '张三'; vm.form.sex = 'male'; vm.form.age = 45
    await vm.submit()
    await flushPromises()
    expect(spy).not.toHaveBeenCalled()
    expect(w.text()).toContain('请选择要上传的 PDF 或报告照片')
  })

  it('上传失败显示引导文案', async () => {
    vi.spyOn(api, 'createReportFromFile').mockRejectedValue(new Error('boom'))
    const { w, vm } = setup()
    vm.form.name = '张三'; vm.form.sex = 'male'; vm.form.age = 45
    vm.onFileChange({ raw: PDF })
    await vm.submit()
    await flushPromises()
    expect(w.text()).toContain('这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。')
  })
})
