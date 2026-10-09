<script setup lang="ts">
import { computed } from 'vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatConversation from '@/components/chat/ChatConversation.vue'
import { useDemoChat } from '@/composables/useDemoChat'
import { useIntelligenceStore } from '@/stores/intelligence'
const store = useIntelligenceStore()
const { messages, sending, submit, reset, userToken } = useDemoChat(() => store.items)
const hasConversation = computed(() => messages.value.some(message => message.role === 'user'))
</script>
<template>
  <div :class="['qa-page', { 'has-conversation': hasConversation }]">
  <div class="page-heading qa-heading">
    <div><h1>可追溯问答</h1><p>输入漏洞编号或安全问题，查看回答并核对引用来源。</p></div>
    <button class="primary-link" type="button" :disabled="sending" @click="reset">新会话</button>
  </div>
  <div class="qa-layout">
    <section class="panel chat-panel" aria-label="情报问答">
      <div class="qa-context">{{ store.isDemo ? '虚构样本范围' : '真实资料范围' }} · 回答附来源，可继续追问</div>
      <ChatConversation :messages="messages" :sending="sending" />
      <ChatComposer :disabled="!userToken" :sending="sending" @submit="submit" />
    </section>
    <section v-if="!userToken" class="qa-authorization">
      <h3>用户授权</h3><p>填写后端签发的短期用户令牌。</p>
      <input v-model="userToken" type="password" autocomplete="off" aria-label="短期用户令牌" />
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
