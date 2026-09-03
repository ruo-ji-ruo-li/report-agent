<!-- src/views/NotFoundView.vue -->
<script setup lang="ts">
</script>

<template>
  <main class="page-col notfound">
    <h1>没有这个页面</h1>
    <el-button type="primary" @click="$router.push('/')">回首页</el-button>
  </main>
</template>

<style scoped>
.notfound { padding-top: var(--space-48); }
</style>
