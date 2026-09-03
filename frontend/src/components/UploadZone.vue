<!-- src/components/UploadZone.vue —— 文件上传(spec-f §5.1/§7) -->
<script setup lang="ts">
import { ref } from 'vue'
import { createReportFromFile } from '../api/reports'

const emit = defineEmits<{ uploaded: [{ reportId: string; taskId: string; source: 'pdf' | 'photo' }] }>()
const uploading = ref(false)
const errorMessage = ref('')

function sourceOf(file: File): 'pdf' | 'photo' {
  return file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'photo'
}

async function handleFile(file: File) {
  if (uploading.value) return // 在途守卫:上传中忽略再拖入/选择,防并发双 POST(fix round 1)
  uploading.value = true
  errorMessage.value = ''
  try {
    const { report_id, task_id } = await createReportFromFile(file)
    emit('uploaded', { reportId: report_id, taskId: task_id, source: sourceOf(file) })
  } catch (err) {
    // 失败文案渲染在拖拽区内部(brief 接口注释 + 测试都以组件内 DOM 为准;
    // ElMessage 服务挂到 document.body,组件 test 不可见,故弃用)
    console.error('upload_failed', err) // 原始异常落 console,便于线上排障(spec-f §7)
    errorMessage.value = '这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。'
  } finally {
    uploading.value = false
  }
}

/** el-upload on-change 回调(uploadFile 为原始 File,含 .raw 字段) */
function onFileChange(uploadFile: { raw: File }) {
  handleFile(uploadFile.raw)
}

defineExpose({ handleFile })
</script>

<template>
  <div class="upload-zone">
    <el-upload drag :show-file-list="false" :auto-upload="false"
               accept=".pdf,.jpg,.jpeg,.png" :on-change="onFileChange">
      <div class="drop-hint">{{ uploading ? '正在上传…' : '拖入 PDF 或报告照片,或点击选择文件' }}</div>
      <p v-if="errorMessage" class="upload-error">{{ errorMessage }}</p>
    </el-upload>
  </div>
</template>

<style scoped>
.upload-zone :deep(.el-upload-dragger) {
  background: transparent;
  border: 1px dashed var(--c-hairline);
  border-radius: 4px;
  padding: var(--space-48) var(--space-16);
}
.drop-hint { color: var(--c-ink); opacity: 0.6; }
.upload-error { margin: 0; padding-top: var(--space-8); color: var(--c-brick); }
</style>
