<!-- src/components/SectionItems.vue —— 两栏条带行(spec-f §6.5) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { InterpretationDoc, NormalizedItem } from '../api/types'
import { joinInterpretation } from '../utils/joinInterpretation'
import { adviceMeta } from '../utils/severity'
import MarkdownBlock from './MarkdownBlock.vue'
import RangeStrip from './RangeStrip.vue'

const props = defineProps<{ doc: InterpretationDoc; normalized: NormalizedItem[] }>()

const joined = computed(() => joinInterpretation(props.doc.items, props.normalized))

function toneClass(tone: string): string {
  return `tag-${tone}`
}
</script>

<template>
  <section class="section">
    <div class="head-row">
      <h2 class="section-title">逐项解读</h2>
      <span class="legend">— 参考区间 ● 你的结果</span>
    </div>
    <div v-for="(j, i) in joined" :key="i" class="item-band" :class="{ 'hairline-top': i > 0 }">
      <div class="data-col">
        <div class="item-name">{{ j.interp.name }}</div>
        <div class="mono item-value">{{ j.interp.value_text }}</div>
        <RangeStrip v-if="j.norm" :value="j.norm.value_num" :low="j.norm.ref_low"
                    :high="j.norm.ref_high" :status="j.norm.status" :critical="j.norm.critical" />
      </div>
      <div class="text-col">
        <MarkdownBlock :text="j.interp.meaning" />
        <ul v-if="j.interp.risks.length" class="risks">
          <li v-for="(r, k) in j.interp.risks" :key="k">{{ r }}</li>
        </ul>
        <div class="advice-line">
          <span class="tag" :class="toneClass(adviceMeta(j.interp.advice_level).tone)">
            {{ adviceMeta(j.interp.advice_level).label }}
          </span>
          <span>{{ j.interp.advice }}</span>
        </div>
        <div v-if="j.interp.evidence_ids.length" class="evidence-note">
          引用知识库 {{ j.interp.evidence_ids.length }} 条
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: baseline; }
.section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 var(--space-16);
}
.legend { font-size: 13px; opacity: 0.6; }
.item-band { display: flex; gap: var(--space-16); padding: var(--space-16) 0; }
.data-col { width: 320px; flex-shrink: 0; }
.item-name { font-weight: 600; }
.item-value { font-size: 15px; margin: var(--space-8) 0; }
.risks { margin: 0 0 var(--space-8); padding-left: 1.5em; }
.advice-line { display: flex; gap: var(--space-8); align-items: baseline; }
.tag { font-size: 12px; padding: 0 6px; border-radius: 4px; color: #fff; flex-shrink: 0; }
.tag-brick { background: var(--c-brick); }
.tag-amber { background: var(--c-amber); }
.tag-clinical { background: var(--c-clinical); }
.evidence-note { font-size: 12px; opacity: 0.5; margin-top: var(--space-8); }
</style>
