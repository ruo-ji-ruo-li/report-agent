<!-- src/components/UploadZone.vue —— 表单元数据 + 文件上传(spec §11.2) -->
<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { FormInstance } from 'element-plus'
import { createReportFromFile, type UploadMeta } from '../api/reports'

const emit = defineEmits<{ uploaded: [{ reportId: string; taskId: string; source: 'pdf' | 'photo' }] }>()
const formRef = ref<FormInstance>()
const uploading = ref(false)
const errorMessage = ref('')
const file = ref<File | null>(null)
const form = reactive({ name: '', sex: '', age: null as number | null, institution: '', report_date: '' })
// name/sex/age 必填,未填写 form-item 就地报校验错误(spec §11.2)
const rules = {
  name: [{ required: true, message: '请填写姓名', trigger: 'blur' }],
  sex: [{ required: true, message: '请选择性别', trigger: 'change' }],
  age: [{ required: true, message: '请填写年龄', trigger: 'change' }],
}

function sourceOf(f: File): 'pdf' | 'photo' {
  return f.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'photo'
}

function onFileChange(uploadFile: { raw: File }) {
  file.value = uploadFile.raw
}

async function submit() {
  if (uploading.value) return // 在途守卫:防并发双 POST(fix round 1)
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return // 必填项为空:form-item 已就地显示校验错误
  if (!file.value) {
    errorMessage.value = '请选择要上传的 PDF 或报告照片'
    return
  }
  uploading.value = true
  errorMessage.value = ''
  try {
    const meta: UploadMeta = {
      name: form.name, sex: form.sex, age: form.age as number,
      institution: form.institution || undefined, report_date: form.report_date || undefined,
    }
    const { report_id, task_id } = await createReportFromFile(file.value, meta)
    emit('uploaded', { reportId: report_id, taskId: task_id, source: sourceOf(file.value) })
  } catch (err) {
    // 失败文案渲染在组件内部(brief 接口注释 + 测试都以组件内 DOM 为准)
    console.error('upload_failed', err)
    errorMessage.value = '这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。'
  } finally {
    uploading.value = false
  }
}

defineExpose({ submit, onFileChange, form })
</script>

<template>
  <div class="upload-zone">
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <div class="meta-row">
        <el-form-item label="姓名" prop="name" class="meta-field">
          <el-input v-model="form.name" placeholder="必填" />
        </el-form-item>
        <el-form-item label="性别" prop="sex" class="meta-field">
          <el-select v-model="form.sex" placeholder="必填">
            <el-option label="男" value="male" /><el-option label="女" value="female" />
          </el-select>
        </el-form-item>
        <el-form-item label="年龄" prop="age" class="meta-field">
          <el-input-number v-model="form.age" :min="0" :max="120" placeholder="必填" />
        </el-form-item>
        <el-form-item label="检测机构" class="meta-field">
          <el-input v-model="form.institution" placeholder="选填" />
        </el-form-item>
        <el-form-item label="报告日期" class="meta-field">
          <el-date-picker v-model="form.report_date" type="date" value-format="YYYY-MM-DD" placeholder="选填" />
        </el-form-item>
      </div>
      <el-form-item label="报告文件(PDF 或照片)">
        <el-upload drag :show-file-list="false" :auto-upload="false" :limit="1"
                   accept=".pdf,.jpg,.jpeg,.png" :on-change="onFileChange">
          <div class="drop-hint">{{ uploading ? '正在上传…' : '拖入 PDF 或报告照片,或点击选择文件' }}</div>
        </el-upload>
      </el-form-item>
      <p v-if="errorMessage" class="upload-error">{{ errorMessage }}</p>
      <el-button type="primary" native-type="submit" :loading="uploading" @click="submit">上传并解读</el-button>
    </el-form>
  </div>
</template>

<style scoped>
.upload-zone :deep(.el-upload-dragger) {
  background: transparent;
  border: 1px dashed var(--c-hairline);
  border-radius: 4px;
  padding: var(--space-48) var(--space-16);
}
.meta-row { display: flex; gap: var(--space-16); flex-wrap: wrap; }
.meta-field { flex: 1; min-width: 140px; }
.drop-hint { color: var(--c-ink); opacity: 0.6; }
.upload-error { margin: 0; padding-top: var(--space-8); color: var(--c-brick); }
</style>
