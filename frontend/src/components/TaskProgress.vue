<!-- src/components/TaskProgress.vue —— 7 阶段细进度带(spec-f §5.1/§6.3) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { TaskInfo } from '../api/types'

const props = defineProps<{ info: TaskInfo }>()

const STAGES = [
  { key: 'parse', label: '解析报告' },
  { key: 'normalize', label: '标准化' },
  { key: 'compare', label: '规则判定' },
  { key: 'retrieve', label: '检索知识' },
  { key: 'generate', label: '生成解读' },
  { key: 'guardrail', label: '安全校验' },
  { key: 'plan', label: '复查计划' },
] // 阶段 key 对齐后端 STAGE_ORDER(spec-f §5.1)

const currentIndex = computed(() =>
  props.info.stage ? STAGES.findIndex(s => s.key === props.info.stage) : -1,
)
const elapsed = computed(() =>
  props.info.timings ? Math.round(Object.values(props.info.timings).reduce((a, b) => a + b, 0)) : null,
)
</script>

<template>
  <div class="task-progress">
    <div class="stage-line">
      <template v-for="(s, i) in STAGES" :key="s.key">
        <span class="stage-seg" :class="{
          done: info.completed_stages.includes(s.key),
          pulse: i === currentIndex,
        }" />
        <span v-if="i < STAGES.length - 1" class="stage-gap" />
      </template>
    </div>
    <div class="stage-meta">
      <span>{{ info.stage ? STAGES[currentIndex]?.label ?? '处理中' : '排队中' }}</span>
      <span v-if="elapsed !== null" class="mono">已用 {{ elapsed }}s</span>
    </div>
  </div>
</template>

<style scoped>
.stage-line { display: flex; align-items: center; }
.stage-seg { flex: 1; height: 4px; border-radius: 2px; background: var(--c-hairline); }
.stage-gap { width: 4px; }
.stage-seg.done { background: var(--c-clinical); }
/* 动画类挂 pulse(而非 current):base.css 的 prefers-reduced-motion 规则只杀 .pulse,
   类名对齐后该可访问性规则才真正生效(spec-f §10 验收项) */
.stage-seg.pulse { background: var(--c-clinical); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.stage-meta { display: flex; justify-content: space-between; font-size: 13px; opacity: 0.6; margin-top: var(--space-8); }
</style>
