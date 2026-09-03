<!-- src/components/ManualEntryForm.vue —— JSON 手动录入(spec-f §5.1,字段对齐 ManualItem) -->
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { createReportManual } from '../api/reports'
import type { ManualEntry, ManualItem } from '../api/types'

const emit = defineEmits<{ uploaded: [{ reportId: string; taskId: string; source: 'manual' }] }>()

const sex = ref<string>('')
const age = ref<number | null>(null)
const institution = ref('')
const reportDate = ref('')
const rows = reactive<ManualItem[]>([{ name: '' }])
const submitting = ref(false)
// 校验/失败提示用组件内联渲染:ElMessage 服务挂到 document.body,组件 test 的 w.text() 不可见,
// 故与 UploadZone 同一裁定改用内联 ref 展示(ruling from Task 9)
const hint = ref('')
const errorMessage = ref('')

function addRow() {
  rows.push({ name: '' })
}
function removeRow(i: number) {
  rows.splice(i, 1)
}

async function submit() {
  if (submitting.value) return // 重入守卫:submit 按钮 @click 与原生 form submit 都会触发,防双 POST
  const items = rows.filter(r => r.name.trim())
  hint.value = ''
  errorMessage.value = ''
  if (items.length === 0) {
    hint.value = '至少填写一条检验项目'
    return
  }
  submitting.value = true
  const entry: ManualEntry = {
    meta: { sex: sex.value || null, age: age.value ?? null,
            institution: institution.value || null, report_date: reportDate.value || null },
    items,
  }
  try {
    const { report_id, task_id } = await createReportManual(entry)
    emit('uploaded', { reportId: report_id, taskId: task_id, source: 'manual' })
  } catch {
    errorMessage.value = '这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。'
  } finally {
    submitting.value = false
  }
}

defineExpose({ rows, sex, age, submit })
</script>

<template>
  <el-form label-position="top" @submit.prevent="submit">
    <div class="meta-row">
      <el-form-item label="性别" class="meta-field">
        <el-select v-model="sex" clearable placeholder="未知">
          <el-option label="男" value="男" /><el-option label="女" value="女" />
        </el-select>
      </el-form-item>
      <el-form-item label="年龄" class="meta-field">
        <el-input-number v-model="age" :min="0" :max="120" placeholder="未知" />
      </el-form-item>
      <el-form-item label="体检机构" class="meta-field">
        <el-input v-model="institution" placeholder="选填" />
      </el-form-item>
      <el-form-item label="报告日期" class="meta-field">
        <el-date-picker v-model="reportDate" type="date" value-format="YYYY-MM-DD" placeholder="选填" />
      </el-form-item>
    </div>

    <div class="item-head">检验项目(至少一条)</div>
    <div v-for="(row, i) in rows" :key="i" class="item-row">
      <el-input v-model="row.name" placeholder="项目名,如:总胆固醇" class="field-name" />
      <el-input v-model="row.value_text" placeholder="数值,如:6.31" class="field" />
      <el-input v-model="row.unit" placeholder="单位" class="field" />
      <el-input v-model="row.ref_range_text" placeholder="参考范围" class="field" />
      <el-button class="btn-del" text type="danger" :disabled="rows.length === 1" @click="removeRow(i)">删</el-button>
    </div>
    <el-button class="btn-add" text type="primary" @click="addRow">+ 添加一行</el-button>

    <div class="submit-row">
      <p v-if="hint" class="form-hint">{{ hint }}</p>
      <p v-if="errorMessage" class="form-error">{{ errorMessage }}</p>
      <!-- @click 兜底:happy-dom 不实现 type=submit 按钮的原生 form 提交(探针验证),
           测试 trigger('click') 需组件显式监听;真实浏览器中按钮点击与原生 form submit
           由 submit() 顶部的 submitting 重入守卫去重 -->
      <el-button type="primary" native-type="submit" :loading="submitting" @click="submit">提交并解读</el-button>
    </div>
  </el-form>
</template>

<style scoped>
.meta-row { display: flex; gap: var(--space-16); flex-wrap: wrap; }
.meta-field { flex: 1; min-width: 140px; }
.item-head { font-size: 13px; opacity: 0.6; margin: var(--space-16) 0 var(--space-8); }
.item-row { display: flex; gap: var(--space-8); margin-bottom: var(--space-8); align-items: center; }
.field-name { flex: 2; }
.field { flex: 1; }
.submit-row { margin-top: var(--space-16); }
.form-hint { margin: 0 0 var(--space-8); font-size: 13px; color: var(--c-ink); opacity: 0.7; }
.form-error { margin: 0 0 var(--space-8); font-size: 13px; color: var(--c-brick); }
</style>
