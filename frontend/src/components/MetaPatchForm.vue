<!-- src/components/MetaPatchForm.vue —— 性别/年龄补录(spec-f §5.1/§7) -->
<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { patchReportMeta } from '../api/reports'

const props = defineProps<{ reportId: string }>()
const emit = defineEmits<{ saved: [] }>()

const sex = ref<string>('')
const age = ref<number | null>(null)
const saving = ref(false)

async function save() {
  if (!sex.value && age.value === null) {
    ElMessage.warning('请至少填写性别或年龄')
    return
  }
  saving.value = true
  try {
    await patchReportMeta(props.reportId, { sex: sex.value || undefined, age: age.value ?? undefined })
    emit('saved')
  } catch {
    ElMessage.error('保存失败,请稍后重试')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="meta-patch">
    <div class="patch-title">报告缺少性别或年龄,补充后继续解读</div>
    <div class="patch-row">
      <el-select v-model="sex" placeholder="性别">
        <el-option label="男" value="男" /><el-option label="女" value="女" />
      </el-select>
      <el-input-number v-model="age" :min="0" :max="120" placeholder="年龄" />
      <el-button type="primary" :loading="saving" @click="save">保存并继续解读</el-button>
    </div>
  </div>
</template>

<style scoped>
.patch-title { margin-bottom: var(--space-8); }
.patch-row { display: flex; gap: var(--space-8); align-items: center; }
</style>
