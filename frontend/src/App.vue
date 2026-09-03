<!-- src/App.vue —— 页眉壳(产品名小字)+ 路由出口 -->
<script setup lang="ts">
</script>

<template>
  <header class="site-header hairline-top">
    <div class="page-col site-header-inner">
      <RouterLink to="/" class="brand">体检报告解读助手</RouterLink>
    </div>
  </header>
  <RouterView />
</template>

<style scoped>
.site-header { padding: var(--space-16) 0; border-top: none; border-bottom: 1px solid var(--c-hairline); }
.site-header-inner { display: flex; align-items: center; }
.brand { font-size: 13px; color: var(--c-ink); text-decoration: none; opacity: 0.6; }
.brand:hover { opacity: 1; }
</style>
