// tests/joinInterpretation.test.ts
import { describe, expect, it } from 'vitest'
import { joinInterpretation } from '../src/utils/joinInterpretation'
import type { InterpretationItem, NormalizedItem } from '../src/api/types'

const interp = (name: string, code: string | null): InterpretationItem => ({
  indicator_code: code, name, status: 'high', value_text: `${name} 6.31`,
  meaning: 'm', risks: [], advice_level: 'recheck', advice: 'a', evidence_ids: [],
})
const norm = (item_name: string, code: string | null): NormalizedItem => ({
  item_name, indicator_code: code, value_num: 6.31, unit: 'mmol/L',
  status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null,
})

describe('joinInterpretation(spec-f §6.4)', () => {
  it('优先按 indicator_code 关联', () => {
    const joined = joinInterpretation([interp('总胆固醇', 'TC')], [norm('胆固醇', 'TC')])
    expect(joined[0].norm?.item_name).toBe('胆固醇')
  })

  it('code 缺失时按名称关联', () => {
    const joined = joinInterpretation([interp('总胆固醇', null)], [norm('总胆固醇', null)])
    expect(joined[0].norm).not.toBeNull()
  })

  it('无匹配 norm 为 null(该行不画带)', () => {
    const joined = joinInterpretation([interp('神秘指标', null)], [norm('ALT', 'ALT')])
    expect(joined[0].norm).toBeNull()
  })

  it('一个 norm 只被用一次', () => {
    const joined = joinInterpretation([interp('甲', 'X'), interp('乙', 'X')], [norm('甲', 'X')])
    expect(joined[0].norm).not.toBeNull()
    expect(joined[1].norm).toBeNull()
  })
})
