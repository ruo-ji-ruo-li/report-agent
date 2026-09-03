<!-- src/components/SectionSummary.vue -->
<script setup lang="ts">
import MarkdownBlock from './MarkdownBlock.vue'
defineProps<{ text: string }>()
</script>

<template>
  <section class="section">
    <h2 class="section-title">总体结论</h2>
    <MarkdownBlock :text="text" />
  </section>
</template>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 var(--space-16);
}
</style>
