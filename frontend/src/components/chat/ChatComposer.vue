<script setup lang="ts">
import { shallowRef } from 'vue'

defineProps<{ disabled: boolean; sending: boolean }>()
const emit = defineEmits<{ submit: [question: string] }>()
const question = shallowRef('')
const suggestions = [
  '智能体工具审计有什么风险？',
  '提示注入应如何处理？',
  '访问控制相关情报有哪些？',
]
function submit(value = question.value) {
  const clean = value.trim()
  if (!clean) return
  emit('submit', clean)
  question.value = ''
}
</script>

<template>
  <div class="chat-compose">
    <div class="suggestions" aria-label="示例问题">
      <button
        v-for="sample in suggestions"
        :key="sample"
        type="button"
        :disabled="disabled || sending"
        @click="submit(sample)"
      >
        {{ sample }}
      </button>
    </div>
    <form @submit.prevent="submit()">
      <label class="sr-only" for="chat-question">输入问题</label
      ><input
        id="chat-question"
        v-model="question"
        :disabled="disabled || sending"
        name="question"
        autocomplete="off"
        maxlength="300"
        placeholder="例如：智能体工具调用有哪些风险？"
      /><button type="submit" :disabled="disabled || sending || !question.trim()">
        {{ sending ? '生成中…' : '发送 ↗' }}
      </button>
    </form>
    <small>仅使用本地演示记录进行关键词匹配；回答可能不完整或不准确。</small>
  </div>
</template>
