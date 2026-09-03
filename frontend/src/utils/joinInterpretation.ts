import type { InterpretationItem, NormalizedItem } from '../api/types'

export interface JoinedItem { interp: InterpretationItem; norm: NormalizedItem | null }

/** 解读条目与归一化数值按 code → 名称关联;norm 只被用一次(spec-f §6.4)。 */
export function joinInterpretation(interp: InterpretationItem[], norm: NormalizedItem[]): JoinedItem[] {
  const unused = [...norm]
  const take = (pred: (n: NormalizedItem) => boolean): NormalizedItem | null => {
    const i = unused.findIndex(pred)
    if (i === -1) return null
    return unused.splice(i, 1)[0]
  }
  return interp.map(it => {
    const n = (it.indicator_code
      ? take(x => x.indicator_code === it.indicator_code)
      : null) ?? take(x => x.item_name === it.name)
    return { interp: it, norm: n }
  })
}
