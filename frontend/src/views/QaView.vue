<script setup lang="ts">
import assistantAvatar from '@/assets/assistant-avatar.svg'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatConversation from '@/components/chat/ChatConversation.vue'
import { useDemoChat } from '@/composables/useDemoChat'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const { messages, sending, submit } = useDemoChat(() => store.items)
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">工作空间 / 情报问答</span>
      <h1>可追溯问答</h1>
      <p>多轮对话界面与情报引用展示。</p>
    </div>
    <span class="simulated-label">模拟回答 · 未接入 RAG</span>
  </div>
  <div class="qa-layout">
    <section class="panel chat-panel" aria-label="演示问答">
      <div class="chat-header">
        <span class="avatar assistant-avatar" aria-hidden="true">
          <img :src="assistantAvatar" class="chat-avatar-image" alt="" width="36" height="36" />
        </span>
        <div><strong>安全情报助手</strong><small>本地关键词匹配演示</small></div>
        <span class="chat-online">演示模式</span>
      </div>
      <ChatConversation :messages="messages" :sending="sending" />
      <ChatComposer
        :disabled="!store.initialized || !!store.error"
        :sending="sending"
        @submit="submit"
      />
    </section>
    <aside class="qa-aside">
      <section class="panel callout-panel">
        <span class="eyebrow">使用说明</span>
        <h2>演示范围</h2>
        <p>
          输入问题后，前端根据关键词从本地情报中选取记录，并展示对应详情页链接。对话保存在当前页面内，刷新后清空。
        </p>
        <div class="aside-divider" />
        <h3>真实联调待确认</h3>
        <p>会话接口、引用字段、流式协议和错误格式。</p>
      </section>
    </aside>
  </div>
</template>
