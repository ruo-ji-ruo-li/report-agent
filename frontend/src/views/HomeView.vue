<!-- src/views/HomeView.vue —— 首页(spec-f §5.1/§6.6) -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import UploadZone from '../components/UploadZone.vue'
import ManualEntryForm from '../components/ManualEntryForm.vue'
import RangeStrip from '../components/RangeStrip.vue'
import { loadRecent, removeRecent, upsertRecent } from '../utils/recentReports'
import type { RecentReport } from '../utils/recentReports'

const router = useRouter()
const recent = ref<RecentReport[]>([])
const activeTab = ref('file')

onMounted(() => { recent.value = loadRecent() })

function onUploaded(e: { reportId: string; taskId: string; source: 'pdf' | 'photo' | 'manual' }) {
  upsertRecent(localStorage, {
    id: e.reportId, created_at: new Date().toISOString(),
    source: e.source, institution: null, report_date: null, status: null,
  })
  router.push(`/report/${e.reportId}`)
}

function onRemove(id: string) {
  recent.value = removeRecent(localStorage, id)
}

const STATUS_TEXT: Record<string, string> = {
  completed: '已解读', degraded: '已解读(降级)', failed: '解读失败',
  awaiting_meta: '待补充信息', running: '解读中', pending: '排队中',
}
</script>

<template>
  <main class="page-col home">
    <section class="thesis">
      <h1 class="thesis-title">看懂你的体检报告</h1>
      <p class="thesis-sub">上传 PDF 或照片,得到逐项解读与复查建议,还能继续追问</p>
      <div class="example">
        <span class="example-label">示例:总胆固醇 6.31 ↑ 参考 2.8–5.2</span>
        <RangeStrip :value="6.31" :low="2.8" :high="5.2" status="high" :critical="false" />
      </div>
    </section>

    <section class="upload-box">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="上传文件" name="file"><UploadZone @uploaded="onUploaded" /></el-tab-pane>
        <el-tab-pane label="手动录入" name="manual"><ManualEntryForm @uploaded="onUploaded" /></el-tab-pane>
      </el-tabs>
    </section>

    <section class="recent">
      <h2 class="recent-title">最近报告</h2>
      <p v-if="recent.length === 0" class="recent-empty">还没有报告,先上传第一份吧。</p>
      <div v-for="r in recent" :key="r.id" class="recent-row hairline-top">
        <span class="recent-main" @click="router.push(`/report/${r.id}`)">
          <span class="recent-name">{{ r.institution || '手动录入' }}</span>
          <span class="recent-date">{{ r.created_at.slice(0, 10) }}</span>
          <span class="recent-status" :class="`st-${r.status ?? 'pending'}`">
            {{ STATUS_TEXT[r.status ?? 'pending'] }}
          </span>
        </span>
        <el-button text size="small" @click="onRemove(r.id)">删除</el-button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.thesis { margin: var(--space-48) 0; }
.thesis-title { font-family: var(--font-display); font-size: 28px; font-weight: 600; margin: 0 0 var(--space-8); }
.thesis-sub { opacity: 0.7; margin: 0 0 var(--space-16); }
.example { display: flex; align-items: center; gap: var(--space-16); }
.example-label { font-size: 13px; opacity: 0.6; }
.recent { margin-top: var(--space-48); }
.recent-title { font-size: 15px; font-weight: 600; }
.recent-empty { opacity: 0.6; }
.recent-row { display: flex; justify-content: space-between; align-items: center; padding: var(--space-8) 0; }
.recent-main { display: flex; gap: var(--space-16); align-items: baseline; cursor: pointer; }
.recent-status { font-size: 12px; }
.st-completed { color: var(--c-clinical); }
.st-degraded, .st-awaiting_meta, .st-running, .st-pending { color: var(--c-amber); }
.st-failed { color: var(--c-brick); }
</style>
