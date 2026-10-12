<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatConversation from '@/components/chat/ChatConversation.vue'
import { useDemoChat } from '@/composables/useDemoChat'
import { useIntelligenceStore } from '@/stores/intelligence'
import { sessionState } from '@/api/http'
import { readQuestionDraft, saveQuestionDraft } from '@/utils/queryDraft'
const store = useIntelligenceStore()
const { messages, sending, submit, reset, userToken, failedQuestion } = useDemoChat(() => store.items)
const draft = shallowRef(readQuestionDraft(store.isDemo))
watch(draft, value => saveQuestionDraft(store.isDemo, value))
watch(() => store.isDemo, () => { draft.value = readQuestionDraft(store.isDemo) })
const hasConversation = computed(() => messages.value.some(message => message.role === 'user'))
const suggestions = computed(() => {
  if (store.loading || store.error) return ['你能做什么？']
  const items = store.latest.filter(item => item.isDemo === store.isDemo && Boolean(item.summary.trim()) && Boolean(item.title.trim()))
  const identifiers = [...new Set(items.flatMap(item => item.cve && /^CVE-\d{4}-\d+$/i.test(item.cve) ? [item.cve.toUpperCase()] : []))]
  const examples = identifiers.slice(0, 2).map(id => `${id} 是什么问题？请注明来源和资料缺口。`)
  const document = items.find(item => item.documentType !== 'vulnerability' && !item.cve)
  if (document) examples.push(`请概括《${document.title.slice(0, 70)}》记录的内容，并注明来源。`)
  if (examples.length < 3 && identifiers[2]) examples.push(`${identifiers[2]} 的风险是什么？请注明来源。`)
  return examples.length ? examples.slice(0, 3) : ['你能做什么？']
})
const suggestionsHint = computed(() => store.loading ? '资料正在加载，可以先了解助手的能力。'
  : store.error ? '资料列表暂不可用，可以先了解助手的能力或直接输入问题。'
  : !store.items.length ? '尚未加载资料，可以先了解助手的能力或直接输入问题。'
  : suggestions.value[0] === '你能做什么？' ? '当前没有可用的资料示例，可以先了解助手的能力或直接输入问题。'
  : '示例来自当前已加载资料，可回答范围以实际检索证据为准。')
</script>
<template>
  <div :class="['qa-page', { 'has-conversation': hasConversation }]">
  <div class="page-heading qa-heading">
    <div><h1>可追溯问答</h1><p>输入漏洞编号或安全问题，查看回答并核对引用来源。</p></div>
    <button class="primary-link" type="button" :disabled="sending" @click="reset">新会话</button>
  </div>
  <div class="qa-layout">
    <section class="panel chat-panel" aria-label="情报问答">
      <div class="qa-context">{{ store.isDemo ? '虚构样本范围' : '真实资料范围' }} · 支持基础对话，资料问答附来源</div>
      <div v-if="!userToken" class="session-notice" role="status">
        <p>{{ sessionState === 'expired' ? '用户授权已过期，已输入的问题会保留。' : '尚未获得有效的用户授权，可先输入问题。' }}</p>
        <router-link :to="{ path: '/login', query: { returnTo: '/qa' } }">{{ sessionState === 'expired' ? '重新授权' : '前往用户授权' }} ↗</router-link>
      </div>
      <ChatConversation :messages="messages" :sending="sending" />
      <div v-if="failedQuestion" class="failed-query" role="status"><p>上次未完成的问题：{{ failedQuestion }}</p>
        <button class="ghost-button" type="button" :disabled="sending || !userToken" @click="submit(failedQuestion)">重试此问题</button>
        <button class="ghost-button" type="button" :disabled="sending" @click="draft = failedQuestion">放回输入框</button>
        <router-link v-if="!userToken" :to="{ path: '/login', query: { returnTo: '/qa' } }">授权后重试 ↗</router-link>
      </div>
      <ChatComposer v-model="draft" :disabled="!userToken" :sending="sending" :suggestions="suggestions" :suggestions-hint="suggestionsHint" @submit="submit" />
    </section>
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
  </div>
</template>
