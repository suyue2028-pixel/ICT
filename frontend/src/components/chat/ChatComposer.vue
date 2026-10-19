<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ modelValue: string; disabled: boolean; sending: boolean }>()
const emit = defineEmits<{ submit: [question: string]; 'update:modelValue': [value: string] }>()
const question = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
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
    <form @submit.prevent="submit()">
      <label class="sr-only" for="chat-question">输入问题</label
      ><textarea
        id="chat-question"
        v-model="question"
        :disabled="sending"
        name="question"
        autocomplete="off"
        maxlength="4000"
        rows="2"
        placeholder="输入 CVE 编号或你想查询的问题"
        @keydown.enter.exact="enter"
      /><button type="submit" :disabled="disabled || sending || !question.trim()">
        {{ sending ? '回复中…' : '发送' }}
      </button>
    </form>
    <small>Enter 发送，Shift + Enter 换行。安全资料问答附来源，资料不足时会明确说明。{{ disabled ? '可先输入问题，授权后发送。' : '' }}</small>
  </div>
</template>
