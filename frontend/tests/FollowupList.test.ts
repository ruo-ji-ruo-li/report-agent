// tests/FollowupList.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FollowupList from '../src/components/FollowupList.vue'

describe('FollowupList(spec-f §6.6)', () => {
  it('渲染项目/时间/科室/依据四列', () => {
    const w = mount(FollowupList, { props: { doc: {
      items: [{ item: '总胆固醇(危急值)复查', timeframe: '立即', department: '心内科', basis: '达危急值水平' }],
      degraded: false,
    } } })
    expect(w.text()).toContain('总胆固醇(危急值)复查')
    expect(w.text()).toContain('立即')
    expect(w.text()).toContain('心内科')
    expect(w.text()).toContain('达危急值水平')
  })

  it('空计划显示空态文案', () => {
    const w = mount(FollowupList, { props: { doc: { items: [], degraded: false } } })
    expect(w.text()).toContain('本次体检无需特别复查项目')
  })
})
