<script setup lang="ts">
import { nextTick, useTemplateRef, watch } from 'vue'
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
  transport_evidence_truncated: '部分来源摘录超过展示长度，相关生成结论已撤下',
  requested_field_omitted_due_to_size: '部分请求字段超过引用长度限制，请核对原始资料',
  transport_answer_limit: '回答达到长度限制',
  coverage_incomplete: '现有证据仅支持问题的一部分',
  retrieval_failed: '部分资料检索失败，不能据此判断知识库没有相关内容',
  model_generation_failed: '大模型生成失败，当前仅显示已取得的资料证据；这不代表知识库没有相关资料',
  relevance_check_failed: '大模型相关性核验失败，回答未完成；这不代表知识库没有相关资料',
  claim_support_check_failed: '大模型事实核验失败，回答未完成；这不代表知识库没有相关资料',
}
function warningText(warnings: string[]) {
  return [...new Set(warnings.filter(code => !code.startsWith('relevance_filter:')).map(code => warningLabels[code.split(':')[0] || ''] || '部分信息未完整返回'))].join('；')
}
function messageHeading(message: ChatMessage) {
  if (message.role === 'user') return '你的问题'
  if (message.responseKind === 'conversation') return '基础对话'
  if (message.status === 'request_failed') return '请求异常'
  if (message.status === 'insufficient_evidence') return '资料不足'
  return message.status === 'partial' ? '部分资料回答' : '资料回答'
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
    <article v-for="message in messages" :key="message.id" :class="['message-row', message.role, { 'welcome-message': message.id === 'welcome' }]">
      <div class="message-body">
        <div class="message-top">
          <strong>{{ messageHeading(message) }}</strong
          ><span v-if="message.simulated" class="simulated-label small">模拟回答</span
          ><time>{{ message.time }}</time>
        </div>
        <div v-if="message.generationMode" class="simulated-label small">{{ message.generationMode }}</div>
        <div class="message-bubble"><AnswerContent v-if="message.role === 'assistant'" :content="message.content" /><template v-else>{{ message.content }}</template></div>
        <div v-if="message.coverage?.answered_topics?.length || message.coverage?.missing_topics?.length" class="citation-box">
          <div v-if="message.coverage?.answered_topics?.length"><strong>已有证据支持</strong><ul><li v-for="topic in message.coverage?.answered_topics || []" :key="topic">{{ topic }}</li></ul></div>
          <div v-if="message.coverage?.missing_topics?.length"><strong>尚未核实的范围</strong><ul><li v-for="topic in message.coverage?.missing_topics || []" :key="topic">{{ topic }}</li></ul></div>
        </div>
        <p v-if="message.status === 'partial' && !message.coverage?.missing_topics?.length" class="muted">回答尚不完整，请结合原文证据和提示阅读。</p>
        <details v-if="message.evidence?.length" class="citation-box"><summary>核对原文证据（{{ message.evidence.length }}）</summary><div v-for="(item, index) in message.evidence" :key="index"><p>{{ item.content }}</p><small style="overflow-wrap:anywhere">版本 {{ item.content_hash }} · {{ item.locator }}</small><a :href="item.source" target="_blank" rel="noopener noreferrer">原始来源 ↗</a></div></details>
        <p v-if="warningText(message.warnings || [])" class="muted">{{ warningText(message.warnings || []) }}</p>
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
          v-else-if="message.role === 'assistant' && message.id !== 'welcome' && message.responseKind !== 'conversation' && message.status !== 'request_failed'"
          class="citation-empty"
        >
          没有可核验的引用证据
        </div>
      </div>
    </article>
    <div v-if="sending" class="thinking" role="status">正在整理回复…</div>
  </div>
</template>
