// tests/rangeStrip.test.ts
import { describe, expect, it } from 'vitest'
import { stripPosition } from '../src/utils/rangeStrip'

describe('stripPosition(spec-f §6.4)', () => {
  it('双边区间:按比例定位', () => {
    expect(stripPosition(4, 0, 10)).toEqual({ pos: 0.4, out: null })
    expect(stripPosition(0, 0, 10)).toEqual({ pos: 0, out: null })
  })

  it('越界吸附到端点并标记方向', () => {
    expect(stripPosition(12, 0, 10)).toEqual({ pos: 1, out: 'high' })
    expect(stripPosition(-3, 0, 10)).toEqual({ pos: 0, out: 'low' })
  })

  it('仅上界:正常贴右端,越界标 high', () => {
    expect(stripPosition(3, null, 5)).toEqual({ pos: 1, out: null })
    expect(stripPosition(6, null, 5)).toEqual({ pos: 1, out: 'high' })
  })

  it('仅下界:正常贴左端,越界标 low', () => {
    expect(stripPosition(3, 2, null)).toEqual({ pos: 0, out: null })
    expect(stripPosition(1, 2, null)).toEqual({ pos: 0, out: 'low' })
  })

  it('缺失或退化区间返回 null(不画带)', () => {
    expect(stripPosition(5, null, null)).toBeNull()
    expect(stripPosition(5, 10, 10)).toBeNull()
  })
})
