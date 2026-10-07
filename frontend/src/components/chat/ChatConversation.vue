<script setup lang="ts">
import { nextTick, useTemplateRef, watch } from 'vue'
import assistantAvatar from '@/assets/assistant-avatar.svg'
import type { ChatMessage } from '@/types'
import AnswerContent from './AnswerContent.vue'

const props = defineProps<{ messages: ChatMessage[]; sending: boolean }>()
const feed = useTemplateRef<HTMLElement>('feed')
const warningLabels: Record<string, string> = {
  answer_rebuilt_after_citation_validation: '部分引用未通过核对，已改为有效来源摘录',
  citation_excerpt_not_in_backend_revision: '部分引用无法在原始版本中定位',
  source_fields_missing: '来源没有记录部分结构化字段',
  source_assertions_conflict: '不同来源的字段存在分歧',
  tool_trace_incomplete: '部分检索过程记录不完整',
  answer_budget_truncated: '回答达到长度限制，可缩小问题范围后追问',
  transport_answer_truncated: '回答达到传输长度限制',
  transport_answer_limit: '回答达到长度限制',
}
function warningText(warnings: string[]) {
  return [...new Set(warnings.map(code => warningLabels[code] || '部分信息未完整返回'))].join('；')
}
watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    feed.value?.scrollTo({
      top: feed.value.scrollHeight,
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    })
  },
)
</script>

<template>
  <div
    ref="feed"
    class="message-feed"
    role="log"
    aria-label="情报对话"
    aria-live="polite"
    aria-relevant="additions"
  >
    <article v-for="message in messages" :key="message.id" :class="['message-row', message.role]">
      <span
        :class="['avatar', { 'assistant-avatar': message.role === 'assistant' }]"
        aria-hidden="true"
      >
        <img
          v-if="message.role === 'assistant'"
          :src="assistantAvatar"
          class="chat-avatar-image"
          alt=""
          width="36"
          height="36"
        />
        <span v-else>我</span>
      </span>
      <div class="message-body">
        <div class="message-top">
          <strong>{{ message.role === 'assistant' ? '情报助手' : '你' }}</strong
          ><span v-if="message.simulated" class="simulated-label small">模拟回答</span
          ><time>{{ message.time }}</time>
        </div>
        <div v-if="message.generationMode" class="simulated-label small">{{ message.generationMode }}</div>
        <div class="message-bubble"><AnswerContent v-if="message.role === 'assistant'" :content="message.content" /><template v-else>{{ message.content }}</template></div>
        <details v-if="message.evidence?.length" class="citation-box"><summary>核对原文证据（{{ message.evidence.length }}）</summary><div v-for="(item, index) in message.evidence" :key="index"><p>{{ item.content }}</p><small style="overflow-wrap:anywhere">版本 {{ item.content_hash }} · {{ item.locator }}</small><a :href="item.source" target="_blank" rel="noopener noreferrer">原始来源 ↗</a></div></details>
        <p v-if="message.warnings?.length" class="muted">{{ warningText(message.warnings) }}</p>
        <div v-if="message.citations?.length" class="citation-box">
          <span>引用的来源资料</span
          ><router-link
            v-for="citation in message.citations"
            :key="citation.intelligenceId + (citation.contentHash || '')"
            :to="{ path: `/intelligence/${citation.intelligenceId}`, query: citation.contentHash ? { content_hash: citation.contentHash } : {} }"
            >{{ citation.intelligenceId }} · {{ citation.label }} ↗</router-link
          >
        </div>
        <div
          v-else-if="message.role === 'assistant' && message.id !== 'welcome'"
          class="citation-empty"
        >
          引用来源：暂无数据
        </div>
      </div>
    </article>
    <div v-if="sending" class="thinking" role="status">正在检索资料并整理回答…</div>
  </div>
</template>
