<!-- src/components/ChatPanel.vue —— 追问面板(spec-f §5.1/§6.6) -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createChatSession, getChatHistory } from '../api/chat'
import { useChatSSE } from '../composables/useChatSSE'
import MarkdownBlock from './MarkdownBlock.vue'

const props = defineProps<{ reportId: string }>()

const sessionId = ref<string | null>(null)
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const { turns, sending, errorMsg, init, send } = useChatSSE(getChatHistory)

onMounted(async () => {
  try {
    const { session_id } = await createChatSession(props.reportId)
    sessionId.value = session_id
    init(await getChatHistory(session_id))
  } catch {
    ElMessage.error('追问服务暂不可用,请稍后刷新重试')
  }
})

async function doSend() {
  const content = input.value.trim()
  if (!content || sending.value || !sessionId.value) return
  input.value = ''
  await send(sessionId.value, content)
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
}
</script>

<template>
  <div class="chat-panel">
    <div ref="listRef" class="chat-list">
      <div v-for="(t, i) in turns" :key="i" class="turn" :class="t.role">
        <div class="bubble">
          <div v-if="t.role === 'assistant' && t.tool" class="tool-line">{{ t.tool.label }}</div>
          <MarkdownBlock v-if="t.role === 'assistant'" :text="t.text" />
          <template v-else>{{ t.text }}</template>
          <div v-if="t.state === 'safety'" class="safety-note">本条回答经安全校验调整</div>
          <div v-if="t.state === 'error'" class="error-note">生成失败,请重试</div>
        </div>
        <div v-if="t.role === 'assistant' && t.evidence.length" class="evidence">
          <div v-for="(e, k) in t.evidence" :key="k" class="evidence-item">{{ e }}</div>
        </div>
      </div>
      <div v-if="errorMsg" class="error-line">{{ errorMsg }}</div>
    </div>
    <div class="chat-input hairline-top">
      <el-input v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                placeholder="就报告里的任何一项提问"
                :disabled="sending || !sessionId"
                @keydown.enter.exact.prevent="doSend" />
      <el-button type="primary" :disabled="!input.trim() || sending || !sessionId" @click="doSend">发送</el-button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel { display: flex; flex-direction: column; height: 60vh; }
.chat-list { flex: 1; overflow-y: auto; }
.turn { margin: var(--space-16) 0; display: flex; flex-direction: column; }
.turn.user { align-items: flex-end; }
.bubble {
  max-width: 75%;
  padding: var(--space-8) var(--space-16);
  border-radius: 4px;
  background: var(--c-paper);
  border: 1px solid var(--c-hairline);
}
.turn.user .bubble { background: var(--c-mist); border: none; }
.tool-line { font-size: 12px; opacity: 0.5; margin-bottom: var(--space-8); }
.safety-note, .error-note, .error-line { font-size: 12px; opacity: 0.6; margin-top: var(--space-8); }
.evidence { max-width: 75%; margin-top: var(--space-8); font-size: 12px; opacity: 0.6; }
.chat-input { display: flex; gap: var(--space-8); padding-top: var(--space-16); align-items: flex-end; }
</style>
