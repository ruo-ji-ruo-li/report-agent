// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SectionItems from '../src/components/SectionItems.vue'
import type { InterpretationDoc, NormalizedItem } from '../src/api/types'

const doc: InterpretationDoc = {
  summary: 's',
  items: [{
    indicator_code: 'TC', name: '总胆固醇', status: 'high',
    value_text: '6.31 mmol/L(参考区间 2.8~5.2)', meaning: '高于参考区间',
    risks: [], advice_level: 'recheck', advice: '建议低脂饮食', evidence_ids: ['e0'],
  }],
  advice_summary: '', disclaimer: '', degraded: false,
}
const norm: NormalizedItem = {
  item_name: '总胆固醇', indicator_code: 'TC', value_num: 6.31, unit: 'mmol/L',
  status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null,
}

describe('SectionItems(spec-f §6.5)', () => {
  it('两栏条带行:数值/区间带/解读/建议标签/证据脚注', () => {
    const w = mount(SectionItems, { props: { doc, normalized: [norm] } })
    expect(w.text()).toContain('总胆固醇')
    expect(w.text()).toContain('高于参考区间')
    expect(w.text()).toContain('建议低脂饮食')
    expect(w.text()).toContain('定期复查')
    expect(w.text()).toContain('引用知识库 1 条')
    expect(w.find('svg').exists()).toBe(true)
  })

  it('图例只出现一次', () => {
    const w = mount(SectionItems, { props: { doc, normalized: [norm] } })
    // 图例专属短语("你的结果"仅图例含)只出现一次;value_text/meaning 中的
    // "参考区间"字样是数据内容不算图例,故不能以"参考区间"计数(spec-f §6.5)
    expect(w.text().split('你的结果').length - 1).toBe(1)
  })
})
