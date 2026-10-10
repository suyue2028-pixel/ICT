<script setup lang="ts">
import { shallowRef } from 'vue'

const props = withDefaults(defineProps<{ disabled: boolean; sending: boolean; suggestions?: string[]; suggestionsHint?: string }>(), {
  suggestions: () => ['你能做什么？'],
  suggestionsHint: '可以先了解助手的能力，也可以直接输入问题。',
})
const emit = defineEmits<{ submit: [question: string] }>()
const question = shallowRef('')
function submit(value = question.value) {
  if (props.disabled || props.sending) return
  const clean = value.trim()
  if (!clean) return
  emit('submit', clean)
  question.value = ''
}
function enter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229 || props.disabled || props.sending) return
  event.preventDefault()
  submit()
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
    <small v-if="suggestionsHint">{{ suggestionsHint }}</small>
    <form @submit.prevent="submit()">
      <label class="sr-only" for="chat-question">输入问题</label
      ><textarea
        id="chat-question"
        v-model="question"
        :disabled="disabled || sending"
        name="question"
        autocomplete="off"
        maxlength="300"
        rows="2"
        placeholder="输入 CVE 编号或你想查询的问题"
        @keydown.enter.exact="enter"
      /><button type="submit" :disabled="disabled || sending || !question.trim()">
        {{ sending ? '回复中…' : '发送' }}
      </button>
    </form>
    <small>Enter 发送，Shift + Enter 换行。安全资料问答附来源，资料不足时会明确说明。</small>
  </div>
</template>
