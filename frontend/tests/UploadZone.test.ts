// tests/UploadZone.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import UploadZone from '../src/components/UploadZone.vue'
import * as api from '../src/api/reports'

describe('UploadZone', () => {
  it('选择 PDF 文件后调用上传并派发 uploaded 事件', async () => {
    const spy = vi.spyOn(api, 'createReportFromFile').mockResolvedValue({ report_id: 'r1', task_id: 't1' })
    const w = mount(UploadZone, { global: { plugins: [ElementPlus] } })
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' })
    // el-upload 内部 input change 不易模拟:直接调用组件暴露的 handleFile
    await w.vm.$nextTick()
    ;(w.vm as unknown as { handleFile: (f: File) => void }).handleFile(file)
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(file)
    expect(w.emitted('uploaded')![0]).toEqual([{ reportId: 'r1', taskId: 't1', source: 'pdf' }])
  })

  it('上传失败显示引导文案', async () => {
    vi.spyOn(api, 'createReportFromFile').mockRejectedValue(new Error('boom'))
    const w = mount(UploadZone, { global: { plugins: [ElementPlus] } })
    ;(w.vm as unknown as { handleFile: (f: File) => void }).handleFile(new File(['x'], 'a.jpg', { type: 'image/jpeg' }))
    await flushPromises()
    expect(w.text()).toContain('这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。')
  })
})
