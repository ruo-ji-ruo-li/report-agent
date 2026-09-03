// tests/RangeStrip.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RangeStrip from '../src/components/RangeStrip.vue'

describe('RangeStrip(spec-f §6.4)', () => {
  it('数据齐全渲染 SVG 与圆点,偏高为信号橙类', () => {
    const w = mount(RangeStrip, { props: { value: 6.31, low: 2.8, high: 5.2, status: 'high', critical: false } })
    expect(w.find('svg').exists()).toBe(true)
    expect(w.find('circle').exists()).toBe(true)
    expect(w.find('circle').classes()).toContain('dot-amber')
  })

  it('危急值警示砖类 + 箭头', () => {
    const w = mount(RangeStrip, { props: { value: 999, low: 0, high: 40, status: 'critical_high', critical: true } })
    expect(w.find('circle').classes()).toContain('dot-brick')
    expect(w.find('text').text()).toBe('↑')
  })

  it('参考区间缺失时不渲染', () => {
    const w = mount(RangeStrip, { props: { value: 5, low: null, high: null, status: 'normal', critical: false } })
    expect(w.find('svg').exists()).toBe(false)
  })
})
