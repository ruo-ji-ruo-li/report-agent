// src/utils/severity.ts
import type { AdviceLevel, ItemStatus } from '../api/types'

export interface SeverityMeta { dot: 'clinical' | 'amber' | 'brick' | 'dim'; tag: string | null; arrow: 'up' | 'down' | null }
export interface AdviceMeta { label: string; tone: 'brick' | 'amber' | 'clinical' }

/** 圆点颜色/标签/箭头(颜色信号 = 在意等级,spec-f §6.4) */
export function severityOf(status: ItemStatus, critical: boolean): SeverityMeta {
  if (critical || status === 'critical_high' || status === 'critical_low') {
    return { dot: 'brick', tag: '危急', arrow: status.endsWith('low') ? 'down' : 'up' }
  }
  switch (status) {
    case 'high': return { dot: 'amber', tag: null, arrow: 'up' }
    case 'low': return { dot: 'amber', tag: null, arrow: 'down' }
    case 'normal': return { dot: 'clinical', tag: null, arrow: null }
    case 'unknown': return { dot: 'dim', tag: '无法判定', arrow: null }
    case 'unmapped': return { dot: 'dim', tag: '未识别', arrow: null }
  }
}

/** 建议级别 → 中文标签与色调(spec-f §6.5) */
export function adviceMeta(level: AdviceLevel): AdviceMeta {
  switch (level) {
    case 'urgent': return { label: '尽快就医', tone: 'brick' }
    case 'specialist': return { label: '专科就诊', tone: 'amber' }
    case 'recheck': return { label: '定期复查', tone: 'amber' }
    case 'lifestyle': return { label: '生活方式调整', tone: 'clinical' }
  }
}
