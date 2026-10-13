<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import ChatDialoguePanel from '@/components/chat/ChatDialoguePanel.vue'
import ChatHistoryPanel from '@/components/chat/ChatHistoryPanel.vue'
import { useDemoChat } from '@/composables/useDemoChat'
import { useIntelligenceStore } from '@/stores/intelligence'
import { sessionState } from '@/api/http'
import { readQuestionDraft, saveQuestionDraft } from '@/utils/queryDraft'
const store = useIntelligenceStore()
const { messages, sending, restoring, ready, conversationError, sessionId, history,
  submit, reset, openSession, userToken, failedQuestion } = useDemoChat(() => store.items)
const { sessions, loading: historyLoading, error: historyError, hasMore } = history
const draft = shallowRef(readQuestionDraft(store.isDemo))
watch(draft, value => saveQuestionDraft(store.isDemo, value))
watch(() => store.isDemo, () => { draft.value = readQuestionDraft(store.isDemo) })
const hasConversation = computed(() => messages.value.some(message => message.role === 'user'))
function refreshHistory() { history.clear(); void history.loadMore() }
</script>
<template>
  <div :class="['qa-page', { 'has-conversation': hasConversation }]">
  <div class="page-heading qa-heading">
    <div><h1>可追溯问答</h1><p>输入漏洞编号或安全问题，查看回答并核对引用来源。</p></div>
    <button class="primary-link" type="button" :disabled="sending || restoring || !ready" @click="reset">新会话</button>
  </div>
  <div class="qa-layout">
    <div class="qa-main">
    <ChatDialoguePanel v-model="draft" :messages="messages" :sending="sending" :restoring="restoring"
      :error="conversationError" :is-demo="store.isDemo" :session-id="sessionId"
      :composer-disabled="!userToken || !ready || restoring" @submit="submit" />
      <div v-if="!userToken" class="session-notice" role="status">
        <p>{{ sessionState === 'expired' ? '用户授权已过期，已输入的问题会保留。' : '尚未获得有效的用户授权，可先输入问题。' }}</p>
        <router-link :to="{ path: '/login', query: { returnTo: '/qa' } }">{{ sessionState === 'expired' ? '重新登录' : '前往登录' }} ↗</router-link>
      </div>
      <div v-if="failedQuestion" class="failed-query" role="status"><p>上次未完成的问题：{{ failedQuestion }}</p>
        <button class="ghost-button" type="button" :disabled="sending || restoring || !ready || !userToken" @click="submit(failedQuestion)">重试此问题</button>
        <button class="ghost-button" type="button" :disabled="sending" @click="draft = failedQuestion">放回输入框</button>
        <router-link v-if="!userToken" :to="{ path: '/login', query: { returnTo: '/qa' } }">授权后重试 ↗</router-link>
      </div>
    <details class="qa-help">
      <summary>使用说明与资料范围</summary>
      <aside class="qa-aside">
      <section class="panel callout-panel">
        <h2>如何使用</h2>
        <p>1. 输入 CVE 编号或问题。<br />2. 阅读回答，查看原文证据。<br />3. 点击来源，核对内容版本。</p>
        <p>每条回答会标明生成方式；资料不足时会明确说明。</p>
        <div class="aside-divider" /><h3>资料范围</h3>
        <p>{{ store.isDemo ? '当前使用明确标注的虚构测试资料。' : '当前使用后端采集并同步的真实来源资料。' }}</p>
      </section>
    </aside>
    </details>
    </div>
    <ChatHistoryPanel :sessions="sessions" :active-id="sessionId" :loading="historyLoading"
      :error="historyError" :has-more="hasMore" :disabled="sending || restoring || !ready"
      :authorized="!!userToken" @select="openSession" @more="history.loadMore" @refresh="refreshHistory" />
  </div>
  </div>
</template>

<style scoped>

.qa-page { max-width: 1120px; }

.qa-page .qa-heading { margin-top: 12px; }

.qa-page .qa-layout { display: grid; grid-template-columns: minmax(0, 1fr) 258px; gap: 24px; align-items: start; }

.qa-main { min-width: 0; }

.qa-page .qa-help { margin-top: 18px; }

.qa-page .qa-authorization { margin-top: 16px; }

@media (max-width: 1040px) { .qa-page .qa-layout { grid-template-columns: minmax(0, 1fr); } .qa-layout :deep(.qa-history) { order: -1; position: static; } }

@media (max-width: 760px) { .qa-page .qa-layout { gap: 16px; } }

</style>
