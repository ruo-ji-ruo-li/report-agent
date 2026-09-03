// src/utils/rangeStrip.ts
export interface StripPosition { pos: number; out: 'low' | 'high' | null }

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x))
}

/** 值在参考区间带上的位置(spec-f §6.4)。返回 null 表示该行不画带。 */
export function stripPosition(value: number, low: number | null, high: number | null): StripPosition | null {
  if (low === null && high === null) return null
  if (low !== null && high !== null) {
    if (low >= high) return null
    if (value < low) return { pos: 0, out: 'low' }
    if (value > high) return { pos: 1, out: 'high' }
    return { pos: clamp01((value - low) / (high - low)), out: null }
  }
  if (high !== null) {
    return value > high ? { pos: 1, out: 'high' } : { pos: 1, out: null }
  }
  return value < (low as number) ? { pos: 0, out: 'low' } : { pos: 0, out: null }
}
