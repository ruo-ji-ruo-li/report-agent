<!-- src/views/ReportView.vue —— 报告详情(spec-f §5.1/§6.5) -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getFollowupPlan, getInterpretation, getReport } from '../api/reports'
import { getTask } from '../api/tasks'
import type { FollowupPlanDoc, InterpretationDoc, ReportDetail } from '../api/types'
import { createTaskPoller } from '../composables/useTaskPolling'
import { upsertRecent } from '../utils/recentReports'
import TaskProgress from '../components/TaskProgress.vue'
import MetaPatchForm from '../components/MetaPatchForm.vue'
import SectionSummary from '../components/SectionSummary.vue'
import SectionItems from '../components/SectionItems.vue'
import SectionAdvice from '../components/SectionAdvice.vue'
import SectionDisclaimer from '../components/SectionDisclaimer.vue'
import FollowupList from '../components/FollowupList.vue'
import ChatPanel from '../components/ChatPanel.vue'

const route = useRoute()
const reportId = route.params.id as string

const detail = ref<ReportDetail | null>(null)
const taskInfo = ref<Awaited<ReturnType<typeof getTask>> | null>(null)
const interp = ref<InterpretationDoc | null>(null)
const followup = ref<FollowupPlanDoc | null>(null)
const docError = ref<string | null>(null)
const detailError = ref(false) // 详情加载失败(404/网络)→ 渲染错误块而非白屏(fix round 2)
const activeTab = ref('interp')

const terminal = computed(() =>
  detail.value?.task && ['completed', 'degraded', 'failed'].includes(detail.value.task.status),
)

const poller = createTaskPoller(getTask)

// 序号守卫:轮询回调的在途 getReport 返回时若已有更新序号,旧快照作废不覆写(fix round 2)
let loadSeq = 0

/** 拉取详情快照并仅在无更新序号时写入 detail(防慢 tick 旧快照覆写冻结) */
async function refreshDetail() {
  const n = ++loadSeq
  const d = await getReport(reportId)
  if (n === loadSeq) detail.value = d
  return d
}

function updateRecent() {
  if (!detail.value) return
  upsertRecent(localStorage, {
    id: reportId, created_at: new Date().toISOString(),
    source: detail.value.meta.source, institution: detail.value.meta.institution,
    report_date: detail.value.meta.report_date,
    status: detail.value.task?.status ?? null,
  })
}

/** 404 = 尚未生成:重试一次后给 §7 文案(spec-f §5.1) */
async function fetchDoc<T>(fn: () => Promise<T>): Promise<T | null> {
  for (let i = 0; i < 2; i++) {
    try {
      return await fn()
    } catch (e) {
      if ((e as { response?: { status?: number } }).response?.status === 404) {
        await new Promise(r => setTimeout(r, 1000))
        continue
      }
      // 5xx/网络:不冒泡,落 docError 文案(§11 降级不崩溃,fix round 2)
      docError.value = '解读服务暂不可用,稍后刷新页面再试。'
      return null
    }
  }
  docError.value = '解读尚未生成,稍后刷新页面再试。'
  return null
}

async function loadDocs() {
  interp.value = await fetchDoc(() => getInterpretation(reportId))
  followup.value = await fetchDoc(() => getFollowupPlan(reportId))
}

async function startPolling() {
  if (!detail.value?.task) return
  poller.start(detail.value.task.id, {
    onUpdate: async info => { taskInfo.value = info; await refreshDetail() },
    onAwaitingMeta: async info => {
      taskInfo.value = info
      await refreshDetail()
      updateRecent() // awaiting_meta 也是终态之一:不同步则首页条目永远显示旧状态
    },
    onDone: async () => { await refreshDetail(); updateRecent(); await loadDocs() },
    onFailed: async info => {
      taskInfo.value = info
      await refreshDetail()
      updateRecent() // failed 同理:首页条目停在"解读中"误导用户
    },
  })
}

onMounted(async () => {
  try {
    detail.value = await getReport(reportId)
  } catch {
    detailError.value = true // 404/网络:渲染错误块,不白屏(spec §4.1/§11,fix round 2)
    return
  }
  updateRecent()
  if (detail.value.task && !terminal.value) {
    await startPolling()
  } else {
    await loadDocs()
  }
})

onBeforeUnmount(() => poller.stop())
</script>

<template>
  <main v-if="detail" class="page-col report">
    <header class="cover">
      <h1 class="cover-title">体检报告</h1>
      <div class="cover-meta">
        {{ detail.meta.sex ?? '—' }} · {{ detail.meta.age ?? '—' }} 岁
        · {{ detail.meta.institution || '未知机构' }} · {{ detail.meta.report_date || '未知日期' }}
      </div>
      <div class="cover-badges">
        <span v-if="detail.task?.status === 'degraded'" class="badge b-amber">部分能力降级</span>
        <span v-if="detail.task?.status === 'failed'" class="badge b-brick">解读失败</span>
      </div>
    </header>

    <section v-if="detail.task?.status === 'failed'" class="fail-block">
      <p>这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。</p>
      <p v-if="detail.task.error" class="fail-detail">{{ detail.task.error }}</p>
      <el-button type="primary" @click="$router.push('/')">回首页重新上传</el-button>
    </section>

    <section v-else-if="!terminal && taskInfo" class="progress-block">
      <TaskProgress :info="taskInfo" />
      <MetaPatchForm v-if="taskInfo.status === 'awaiting_meta'" :report-id="reportId" @saved="startPolling" />
    </section>

    <section v-else class="content-block">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="解读" name="interp">
          <div v-if="docError" class="doc-error">{{ docError }}</div>
          <template v-if="interp">
            <div v-if="interp.degraded" class="degraded-note">本次解读由备用模板生成,内容为降级结果</div>
            <SectionSummary :text="interp.summary" />
            <SectionItems :doc="interp" :normalized="detail.normalized" />
            <SectionAdvice :text="interp.advice_summary" />
            <SectionDisclaimer :text="interp.disclaimer" />
          </template>
        </el-tab-pane>
        <el-tab-pane label="复查计划" name="followup">
          <FollowupList v-if="followup" :doc="followup" />
        </el-tab-pane>
        <el-tab-pane label="追问" name="chat" lazy>
          <ChatPanel :report-id="reportId" />
        </el-tab-pane>
      </el-tabs>
    </section>
  </main>
  <main v-else-if="detailError" class="page-col report-error">
    <p>报告不存在或服务暂不可用。</p>
    <el-button type="primary" @click="$router.push('/')">回首页</el-button>
  </main>
</template>

<style scoped>
.cover {
  background: var(--c-cover);
  color: var(--c-paper);
  padding: var(--space-48) var(--space-16);
  margin: 0 calc(-1 * var(--space-16));
}
.cover-title { font-family: var(--font-display); font-size: 24px; font-weight: 600; margin: 0 0 var(--space-8); }
.cover-meta { opacity: 0.8; }
.cover-badges { margin-top: var(--space-8); display: flex; gap: var(--space-8); }
.badge { font-size: 12px; padding: 0 6px; border-radius: 4px; color: #fff; }
.b-amber { background: var(--c-amber); }
.b-brick { background: var(--c-brick); }
.progress-block { margin-top: var(--space-48); }
.fail-block { margin-top: var(--space-48); }
.fail-detail { font-size: 13px; opacity: 0.6; }
.doc-error, .degraded-note { padding: var(--space-8) var(--space-16); border-radius: 4px; background: var(--c-mist); margin-bottom: var(--space-16); }
.content-block { margin-top: var(--space-48); }
.report-error { margin-top: var(--space-48); }
</style>
