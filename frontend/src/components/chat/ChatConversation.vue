<script setup lang="ts">
import { nextTick, useTemplateRef, watch } from 'vue'
import type { ChatMessage } from '@/types'
import AnswerContent from './AnswerContent.vue'
import { coverageStatusLabel, questionTypeLabel, stopReasonLabel, traceStatusLabel } from '@/utils/answerWorkflow'
import { isUnverifiedMessage, messageGenerationLabel, messageHeading, showSecurityEvidence, toolLabel, warningText } from '@/utils/chatPresentation'

const props = defineProps<{ messages: ChatMessage[]; sending: boolean }>()
const feed = useTemplateRef<HTMLElement>('feed')
function revealEvidence(messageId: string, id: string) {
  const element = document.getElementById(`evidence-${messageId}-${id}`)
  const details = element?.closest('details')
  if (details) details.open = true
  element?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
}
watch(
  () => [props.messages.at(-1)?.id, props.sending],
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
        <div v-if="messageGenerationLabel(message)" class="simulated-label small">{{ messageGenerationLabel(message) }}</div>
        <div class="message-bubble"><AnswerContent v-if="message.role === 'assistant'" :content="message.content" :plain="message.responseKind === 'conversation' || isUnverifiedMessage(message)" /><template v-else>{{ message.content }}</template></div>
        <router-link v-if="message.failureKind === 'user_auth'" :to="{ path: '/login', query: { returnTo: '/qa' } }" class="retry-authorization">重新授权，保留问题后重试 ↗</router-link>
        <div v-if="showSecurityEvidence(message) && message.workflow?.requirement_coverage.length" class="citation-box requirement-coverage">
          <strong>问题范围与证据覆盖</strong>
          <ul><li v-for="requirement in message.workflow.requirement_coverage" :key="requirement.requirement_id">
            <strong>{{ requirement.label }}</strong> · {{ coverageStatusLabel[requirement.status] }}
            <p v-if="requirement.reason">{{ requirement.reason }}</p>
            <a v-for="id in requirement.evidence_ids" :key="id" :href="`#evidence-${message.id}-${id}`" @click.prevent="revealEvidence(message.id, id)">证据 {{ id }} ↗</a>
          </li></ul>
        </div>
        <div v-else-if="showSecurityEvidence(message) && (message.coverage?.answered_topics?.length || message.coverage?.missing_topics?.length)" class="citation-box">
          <div v-if="message.coverage?.answered_topics?.length"><strong>已有证据支持</strong><ul><li v-for="topic in message.coverage?.answered_topics || []" :key="topic">{{ topic }}</li></ul></div>
          <div v-if="message.coverage?.missing_topics?.length"><strong>尚未核实的范围</strong><ul><li v-for="topic in message.coverage?.missing_topics || []" :key="topic">{{ topic }}</li></ul></div>
        </div>
        <p v-if="showSecurityEvidence(message) && message.status === 'partial' && !message.coverage?.missing_topics?.length" class="muted">回答尚不完整，请结合原文证据和提示阅读。</p>
        <details v-if="showSecurityEvidence(message) && message.evidence?.length" class="citation-box"><summary>核对原文证据（{{ message.evidence.length }}）</summary><div v-for="(item, index) in message.evidence" :id="`evidence-${message.id}-${index + 1}`" :key="index"><strong>证据 {{ index + 1 }}</strong><p>{{ item.content }}</p><small style="overflow-wrap:anywhere">版本 {{ item.content_hash }} · {{ item.locator }}</small><a :href="item.source" target="_blank" rel="noopener noreferrer">原始来源 ↗</a></div></details>
        <details v-if="message.responseKind !== 'conversation' && (message.workflow || message.toolTrace?.length)" class="citation-box execution-record">
          <summary>检索与执行记录{{ message.toolTrace?.length ? `（${message.toolTrace.length}）` : '' }}</summary>
          <p>以下为服务端已经返回的执行记录。</p>
          <template v-if="message.workflow">
            <p v-if="message.workflow.question_type">问题类型：{{ questionTypeLabel(message.workflow.question_type) }}</p>
            <p>检索轮次：{{ message.workflow.retrieval_summary.rounds }} · {{ stopReasonLabel(message.workflow.retrieval_summary.stop_reason) }}</p>
            <p v-if="message.workflow.retrieval_summary.initial_tools.length">初始工具：{{ message.workflow.retrieval_summary.initial_tools.map(toolLabel).join('、') }}</p>
            <p v-if="message.workflow.retrieval_summary.supplemental_tools.length">补充工具：{{ message.workflow.retrieval_summary.supplemental_tools.map(toolLabel).join('、') }}</p>
          </template>
          <ol v-if="message.toolTrace?.length"><li v-for="(trace, index) in message.toolTrace" :key="index">
            <strong>{{ toolLabel(trace.tool) }}</strong> · {{ traceStatusLabel[trace.status] }} · {{ trace.duration_ms }} ms
            <p v-if="trace.input_summary">{{ trace.input_summary }}</p>
            <p v-if="trace.document_ids?.length">返回文档：{{ trace.document_ids.join('、') }}</p>
            <small v-if="trace.error_code">错误代码：{{ trace.error_code }}</small>
          </li></ol>
          <p v-if="message.requestId" class="trace-reference">请求编号：{{ message.requestId }}</p>
        </details>
        <div v-if="message.responseKind !== 'conversation' && message.workflow?.limitations.length" class="citation-box"><strong>资料与运行限制</strong><ul><li v-for="limitation in message.workflow.limitations" :key="limitation">{{ limitation }}</li></ul></div>
        <p v-if="warningText(message.warnings || [])" class="muted">{{ warningText(message.warnings || []) }}</p>
        <div v-if="showSecurityEvidence(message) && message.citations?.length" class="citation-box">
          <span>引用的来源资料</span
          ><router-link
            v-for="citation in message.citations"
            :key="citation.intelligenceId + (citation.contentHash || '')"
            :to="{ path: `/intelligence/${citation.intelligenceId}`, query: citation.contentHash ? { content_hash: citation.contentHash } : {} }"
            >{{ citation.label }} ↗</router-link
          >
        </div>
        <div
          v-else-if="message.role === 'assistant' && message.id !== 'welcome' && showSecurityEvidence(message) && message.status !== 'request_failed'"
          class="citation-empty"
        >
          没有可核验的引用证据
        </div>
      </div>
    </article>
    <div v-if="sending" class="thinking" role="status">请求处理中，正在等待服务端回答与执行记录…</div>
  </div>
</template>

<style scoped>

.message-feed .message-row { flex-direction: row; margin-bottom: 20px; }

.message-feed .message-row.user { justify-content: flex-end; }

.message-feed .message-row.assistant { justify-content: flex-start; }

.message-feed .message-row .message-body { width: fit-content; min-width: 0; border: 1px solid #000; border-radius: 16px; padding: 16px 18px; background: #fff; text-align: left; overflow-wrap: anywhere; }

.message-feed .message-row.user .message-body { max-width: 84%; border-radius: 16px 16px 4px 16px; }

.message-feed .message-row.assistant .message-body { max-width: 92%; border-radius: 16px 16px 16px 4px; }

.message-feed .message-row .message-bubble { background: transparent; border: 0; padding: 0; color: #000; }

.message-feed .message-row .message-top { display: flex; align-items: center; justify-content: flex-start; padding-bottom: 9px; margin-bottom: 10px; border-bottom: 1px solid #000; }


.message-top time { margin-left: auto; }

.message-feed .welcome-message { margin-top: 6px; }

.message-feed .thinking { margin: 0 0 12px; text-align: right; }

@media (max-width: 760px) { .message-feed .message-row .message-body { padding: 12px; } .message-feed .message-row.user .message-body { max-width: 86%; } .message-feed .message-row.assistant .message-body { max-width: 94%; } }

</style>
