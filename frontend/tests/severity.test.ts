// tests/severity.test.ts
import { describe, expect, it } from 'vitest'
import { severityOf, adviceMeta } from '../src/utils/severity'

describe('severityOf', () => {
  it('危急值(含 critical 标志)警示砖 + 危急标签', () => {
    expect(severityOf('high', true)).toEqual({ dot: 'brick', tag: '危急', arrow: 'up' })
    expect(severityOf('critical_high', false)).toEqual({ dot: 'brick', tag: '危急', arrow: 'up' })
    expect(severityOf('critical_low', false)).toEqual({ dot: 'brick', tag: '危急', arrow: 'down' })
  })

  it('高/低为信号橙带箭头,正常为临床青', () => {
    expect(severityOf('high', false)).toEqual({ dot: 'amber', tag: null, arrow: 'up' })
    expect(severityOf('low', false)).toEqual({ dot: 'amber', tag: null, arrow: 'down' })
    expect(severityOf('normal', false)).toEqual({ dot: 'clinical', tag: null, arrow: null })
  })

  it('无法判定/未识别为灰点', () => {
    expect(severityOf('unknown', false)).toEqual({ dot: 'dim', tag: '无法判定', arrow: null })
    expect(severityOf('unmapped', false)).toEqual({ dot: 'dim', tag: '未识别', arrow: null })
  })
})

describe('adviceMeta', () => {
  it('四级建议标签与色调', () => {
    expect(adviceMeta('urgent')).toEqual({ label: '尽快就医', tone: 'brick' })
    expect(adviceMeta('specialist')).toEqual({ label: '专科就诊', tone: 'amber' })
    expect(adviceMeta('recheck')).toEqual({ label: '定期复查', tone: 'amber' })
    expect(adviceMeta('lifestyle')).toEqual({ label: '生活方式调整', tone: 'clinical' })
  })
})
