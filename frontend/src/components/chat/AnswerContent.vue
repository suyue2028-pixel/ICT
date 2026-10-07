<script setup lang="ts">
import { computed } from 'vue'
import { answerBlocks } from '@/utils/answerPresentation'
const props = defineProps<{ content: string }>()
const blocks = computed(() => answerBlocks(props.content))
</script>

<template>
  <div class="answer-content">
    <template v-for="(block, index) in blocks" :key="index">
      <p v-if="block.kind === 'text'" class="answer-paragraph">{{ block.text }}</p>
      <section v-else-if="block.kind === 'quote'" class="answer-source">
        <div class="answer-label"><strong>{{ block.title }}</strong><span>{{ block.reference }}</span></div>
        <blockquote class="answer-quote">{{ block.text }}</blockquote>
      </section>
      <section v-else class="answer-versions">
        <div class="answer-label"><strong>版本信息 · {{ block.title }}</strong><span>{{ block.reference }}</span></div>
        <dl class="answer-fields"><div v-for="field in block.fields" :key="field.label"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl>
        <p v-if="block.incomplete" class="answer-note">返回的版本记录为片段，仅展示其中明确出现的字段；完整范围请核对来源。</p>
        <details class="answer-original"><summary>查看返回的原始版本片段</summary><pre>{{ block.text }}</pre></details>
      </section>
    </template>
  </div>
</template>

<style scoped>
.answer-content { display: grid; gap: 14px; }
.answer-paragraph { margin: 0; white-space: pre-wrap; }
.answer-label { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; font-size: 12px; color: #27354a; }
.answer-label span { color: #5b6d96; }
.answer-quote { margin: 8px 0 0; border-left: 3px solid #b6c4e3; padding: 4px 0 4px 12px; color: #465266; line-height: 1.85; }
.answer-versions { padding: 12px; border: 1px solid #e5eaf2; background: #f8fafd; border-radius: 8px; }
.answer-fields { margin: 10px 0; display: grid; gap: 6px; }
.answer-fields div { display: grid; grid-template-columns: minmax(90px, .8fr) minmax(0, 1.2fr); gap: 12px; }
.answer-fields dt { color: #667085; font-size: 12px; }
.answer-fields dd { margin: 0; font-weight: 600; color: #27354a; overflow-wrap: anywhere; }
.answer-note { font-size: 11px; line-height: 1.7; color: #667085; margin: 8px 0; }
.answer-original { font-size: 11px; color: #596b8a; }
.answer-original summary { cursor: pointer; }
.answer-original pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow: auto; font-family: inherit; }
.answer-original summary:focus-visible { outline: 2px solid #4262ed; outline-offset: 3px; }
</style>
