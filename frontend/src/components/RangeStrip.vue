<!-- src/components/RangeStrip.vue —— 参考区间带(spec-f §6.4 记忆点) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { ItemStatus } from '../api/types'
import { stripPosition } from '../utils/rangeStrip'
import { severityOf } from '../utils/severity'

const props = defineProps<{
  value: number | null
  low: number | null
  high: number | null
  status: ItemStatus
  critical?: boolean
}>()

const W = 160
const PAD = 8

const pos = computed(() =>
  props.value === null ? null : stripPosition(props.value, props.low, props.high),
)
const sev = computed(() => severityOf(props.status, props.critical ?? false))
const cx = computed(() => (pos.value ? PAD + pos.value.pos * (W - PAD * 2) : PAD))
const arrowX = computed(() => (pos.value?.out === 'high' ? W - PAD : PAD))
</script>

<template>
  <svg v-if="pos" class="range-strip" :width="W" height="20" role="img" aria-label="参考区间带">
    <rect class="strip-base" x="0" y="8" :width="W" height="4" rx="2" />
    <circle :class="`dot-${sev.dot}`" :cx="cx" cy="10" r="4" />
    <text v-if="pos.out" :class="`dot-${sev.dot}`" :x="arrowX" y="18"
          text-anchor="middle" font-size="10">{{ pos.out === 'high' ? '↑' : '↓' }}</text>
  </svg>
</template>

<style scoped>
/* 色值全部消费 token(全局约束:组件内不硬编码色值) */
.strip-base { fill: var(--c-mist); }
.dot-clinical { fill: var(--c-clinical); }
.dot-amber { fill: var(--c-amber); }
.dot-brick { fill: var(--c-brick); }
.dot-dim { fill: var(--c-ink); opacity: 0.6; }
</style>
