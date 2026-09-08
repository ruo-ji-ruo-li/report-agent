<!-- src/components/ChatPanel.vue —— 追问面板:左侧会话记录栏 + 右侧聊天区(spec-f §5.1/§6.6) -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createChatSession, getChatHistory, listChatSessions } from '../api/chat'
import type { ChatSessionRow } from '../api/chat'
import { useChatSSE } from '../composables/useChatSSE'
import MarkdownBlock from './MarkdownBlock.vue'

const props = defineProps<{ reportId: string }>()

const sessionId = ref<string | null>(null)
const sessions = ref<ChatSessionRow[]>([])
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const { turns, sending, init, send } = useChatSSE(getChatHistory)

// 切换序号守卫:快速连点多个会话时,慢返回的旧历史不得覆盖新选中的会话(同 ReportView loadSeq 惯例)
let switchSeq = 0

onMounted(async () => {
  try {
    // 进入面板:有历史会话 → 自动恢复最近一个;无 → 自动建新会话兜底
    const rows = await listChatSessions(props.reportId)
    if (rows.length) {
      sessions.value = rows
      await switchSession(rows[0].session_id)
    } else {
      await newSession()
    }
  } catch {
    ElMessage.error('追问服务暂不可用,请稍后刷新重试')
  }
})

/** 切换会话:先取历史、成功才提交 sessionId(高亮与内容始终一致),失败保留现状 */
async function switchSession(id: string) {
  if (sending.value || id === sessionId.value) return
  const n = ++switchSeq
  try {
    const rows = await getChatHistory(id)
    if (n === switchSeq) {
      sessionId.value = id
      init(rows)
    }
  } catch {
    ElMessage.error('历史记录加载失败,请重试')
  }
}

async function newSession() {
  if (sending.value) return
  try {
    const { session_id } = await createChatSession(props.reportId)
    ++switchSeq // 在途的历史响应作废,防止覆盖新会话
    sessionId.value = session_id
    init([])
    // 空会话不入后端列表(接口只含有消息的会话),前端合并占位到顶部;
    // 发出首条消息后 refreshSessions 用真实数据替换
    sessions.value = [placeholder(session_id),
      ...sessions.value.filter(s => s.message_count > 0)]
  } catch {
    ElMessage.error('追问服务暂不可用,请稍后重试')
  }
}

function placeholder(id: string): ChatSessionRow {
  return { session_id: id, created_at: new Date().toISOString(), message_count: 0, preview: '新会话' }
}

/** 发送完成后静默刷新列表(首条消息让新会话入列,占位换真实标题);失败不打扰聊天 */
async function refreshSessions() {
  try {
    const rows = await listChatSessions(props.reportId)
    if (!rows.length) return
    sessions.value = rows
    if (sessionId.value && !rows.some(s => s.session_id === sessionId.value)) {
      sessions.value = [placeholder(sessionId.value), ...sessions.value]
    }
  } catch { /* 列表刷新失败不提示 */ }
}

async function doSend() {
  const content = input.value.trim()
  if (!content || sending.value || !sessionId.value) return
  input.value = ''
  await send(sessionId.value, content)
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
  await refreshSessions()
}

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
</script>

<template>
  <div class="chat-shell">
    <aside class="chat-rail">
      <div class="rail-head">
        <span class="rail-title">追问记录</span>
        <el-button link type="primary" :disabled="sending" @click="newSession">+ 新会话</el-button>
      </div>
      <p v-if="!sessions.length" class="rail-empty">还没有追问记录,问点什么,会留在这里</p>
      <ul v-else class="rail-list">
        <li v-for="s in sessions" :key="s.session_id">
          <button type="button" class="rail-item" :class="{ active: s.session_id === sessionId }"
                  :disabled="sending" @click="switchSession(s.session_id)">
            <span class="rail-item-title">{{ s.preview }}</span>
            <span class="rail-item-meta">{{ fmtTime(s.created_at) }} · {{ s.message_count }} 条</span>
          </button>
        </li>
      </ul>
    </aside>

    <div class="chat-main">
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
      </div>
      <div class="chat-input hairline-top">
        <el-input v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="就报告里的任何一项提问"
                  :disabled="sending || !sessionId"
                  @keydown.enter.exact.prevent="doSend" />
        <el-button type="primary" :disabled="!input.trim() || sending || !sessionId" @click="doSend">发送</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-shell { display: flex; gap: var(--space-16); height: 60vh; }
.chat-rail {
  width: 240px;
  flex: none;
  overflow-y: auto;
  padding-right: var(--space-16);
  border-right: 1px solid var(--c-hairline);
}
.chat-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-8);
}
.rail-title { font-size: 13px; opacity: 0.6; }
.rail-empty { font-size: 13px; opacity: 0.6; margin: var(--space-16) 0 0; }
.rail-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.rail-item {
  display: block;
  width: 100%;
  text-align: left;
  font: inherit;
  background: transparent;
  border: 0;
  border-radius: 4px;
  padding: var(--space-8) 12px;
  cursor: pointer;
}
.rail-item:hover:not(:disabled) { background: var(--c-mist); }
.rail-item.active {
  background: var(--c-mist);
  box-shadow: inset 2px 0 0 var(--c-clinical); /* 选中态:青雾底 + 左缘临床青短线 */
}
.rail-item:disabled { cursor: default; }
.rail-item:focus-visible { outline: 2px solid var(--c-clinical); outline-offset: -2px; }
.rail-item-title {
  display: block;
  font-size: 13px;
  color: var(--c-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rail-item-meta {
  display: block;
  font-size: 12px;
  opacity: 0.6;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
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
.turn.user .bubble {
  background: var(--c-mist);
  border: none;
  white-space: pre-wrap; /* 输入框支持 Shift+Enter 多行;user 气泡为裸插值,需 pre-wrap 保留换行 */
}
.tool-line { font-size: 12px; opacity: 0.5; margin-bottom: var(--space-8); }
.safety-note, .error-note { font-size: 12px; opacity: 0.6; margin-top: var(--space-8); }
.evidence { max-width: 75%; margin-top: var(--space-8); font-size: 12px; opacity: 0.6; }
.chat-input { display: flex; gap: var(--space-8); padding-top: var(--space-16); align-items: flex-end; }
</style>
