<!-- src/components/SectionDisclaimer.vue -->
<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <section class="section disclaimer hairline-top">
    <p>{{ text }}</p>
  </section>
</template>

<style scoped>
.disclaimer { margin-top: var(--space-48); padding-top: var(--space-16); font-size: 13px; opacity: 0.7; }
</style>
