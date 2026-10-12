<script setup lang="ts">
import AssistantMascot from './AssistantMascot.vue'
import ChatComposer from './ChatComposer.vue'
import ChatConversation from './ChatConversation.vue'
import type { ChatMessage } from '@/types'

defineProps<{
  messages: ChatMessage[]
  sending: boolean
  restoring: boolean
  error: string
  isDemo: boolean
  composerDisabled: boolean
  sessionId: string | null
}>()
const draft = defineModel<string>({ required: true })
const emit = defineEmits<{ submit: [question: string] }>()
</script>

<template>
  <div class="qa-dialogue">
    <AssistantMascot :waiting="sending || restoring" />
    <section class="panel chat-panel" aria-label="情报问答" :aria-busy="restoring || sending">
      <svg class="dialogue-tail" width="26" height="36" viewBox="0 0 26 36" aria-hidden="true">
        <path
          d="M26 1L2 18L26 35"
          fill="#FFFFFF"
          stroke="#000000"
          stroke-width="1.5"
          stroke-linejoin="round"
        />
      </svg>
      <div class="qa-context">
        <strong>情报助手</strong>
        <span>{{ isDemo ? '虚构样本范围' : '真实资料范围' }} · 资料问答附来源</span>
      </div>
      <p v-if="restoring" class="conversation-status" role="status">正在打开历史会话…</p>
      <p v-if="error" class="conversation-status" role="alert">{{ error }}</p>
      <ChatConversation :messages="messages" :sending="sending" />
      <ChatComposer
        v-model="draft"
        :key="sessionId || 'new'"
        :disabled="composerDisabled"
        :sending="sending"
        @submit="emit('submit', $event)"
      />
    </section>
  </div>
</template>

<style scoped>
.qa-dialogue {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  align-items: start;
  gap: 24px;
}
.qa-dialogue .chat-panel {
  position: relative;
  min-width: 0;
  min-height: 580px;
  padding: 22px;
  border: 1.5px solid #000;
  border-radius: 22px;
  background: #fff;
  overflow: visible;
}
.dialogue-tail {
  position: absolute;
  top: 26px;
  left: -25px;
  pointer-events: none;
}
.qa-dialogue .qa-context {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 16px;
  padding: 0 0 16px;
  margin-bottom: 8px;
  border-bottom: 1px solid #000;
  color: #000;
}
.qa-context strong {
  font-size: 14px;
  font-weight: 600;
}
.qa-context span {
  font-size: 11px;
}
.conversation-status {
  margin: 10px 0;
  font-size: 12px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.qa-dialogue :deep(.message-feed) {
  min-height: 260px;
  max-height: min(52vh, 580px);
  padding: 18px 2px;
}
.qa-dialogue :deep(.chat-compose) {
  position: static;
  z-index: auto;
  padding-top: 18px;
  border-top: 1px solid #000;
  background: #fff;
}
@media (max-width: 760px) {
  .qa-dialogue {
    grid-template-columns: minmax(0, 1fr);
    gap: 24px;
  }
  .qa-dialogue .chat-panel {
    padding: 16px;
    min-height: 520px;
    border-radius: 18px;
  }
  .dialogue-tail {
    left: 14px;
    top: -30px;
    transform: rotate(90deg);
  }
  .qa-dialogue :deep(.message-feed) {
    min-height: 240px;
    max-height: 55vh;
  }
}
</style>
