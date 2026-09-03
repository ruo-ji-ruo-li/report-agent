<!-- src/components/MarkdownBlock.vue -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ text: string }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const html = ref('')
let timer: ReturnType<typeof setTimeout> | null = null
let lastRender = 0

function render() {
  html.value = DOMPurify.sanitize(md.render(props.text || ''))
  lastRender = Date.now()
}

// 流式更新:80ms 窗内至多渲染一次(窗式节流,不饿死),静默后 80ms 收尾
render()
watch(() => props.text, () => {
  if (timer) return
  const wait = Math.max(0, 80 - (Date.now() - lastRender))
  timer = setTimeout(() => {
    timer = null
    render()
  }, wait)
})

defineExpose({ render })
</script>

<template>
  <div class="md" v-html="html" />
</template>

<style scoped>
.md { word-break: break-word; }
.md :deep(p) { margin: 0 0 var(--space-16); }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(ul) { margin: 0 0 var(--space-16); padding-left: 1.5em; }
.md :deep(code) { font-family: var(--font-mono); font-size: 0.9em; }
</style>
