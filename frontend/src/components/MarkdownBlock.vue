<!-- src/components/MarkdownBlock.vue -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ text: string }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const html = ref('')
let timer: ReturnType<typeof setTimeout> | null = null

function render() {
  html.value = DOMPurify.sanitize(md.render(props.text || ''))
}

// 流式场景 80ms 节流;静态文本仅首帧一次(spec-f §8.5)
render()
watch(() => props.text, () => {
  if (timer) clearTimeout(timer)
  timer = setTimeout(render, 80)
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
