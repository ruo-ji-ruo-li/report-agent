<!-- src/components/FollowupList.vue -->
<script setup lang="ts">
import type { FollowupPlanDoc } from '../api/types'
defineProps<{ doc: FollowupPlanDoc }>()
</script>

<template>
  <div v-if="doc.items.length === 0" class="empty">本次体检无需特别复查项目</div>
  <div v-else class="plan-list">
    <div v-for="(f, i) in doc.items" :key="i" class="plan-row" :class="{ 'hairline-top': i > 0 }">
      <div class="col col-item">{{ f.item }}</div>
      <div class="col col-time mono">{{ f.timeframe }}</div>
      <div class="col col-dept">{{ f.department }}</div>
      <div class="col col-basis">{{ f.basis }}</div>
    </div>
  </div>
</template>

<style scoped>
.plan-row { display: flex; gap: var(--space-16); padding: var(--space-16) 0; }
.col-item { width: 260px; flex-shrink: 0; font-weight: 600; }
.col-time { width: 90px; flex-shrink: 0; }
.col-dept { width: 140px; flex-shrink: 0; }
.col-basis { flex: 1; opacity: 0.8; }
.empty { padding: var(--space-16) 0; opacity: 0.6; }
</style>
