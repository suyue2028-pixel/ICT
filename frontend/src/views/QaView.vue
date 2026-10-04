<script setup lang="ts">
import assistantAvatar from '@/assets/assistant-avatar.svg'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatConversation from '@/components/chat/ChatConversation.vue'
import { useDemoChat } from '@/composables/useDemoChat'
import { useIntelligenceStore } from '@/stores/intelligence'
const store = useIntelligenceStore()
const { messages, sending, submit, reset, userToken } = useDemoChat(() => store.items)
</script>
<template>
  <div class="page-heading">
    <div><span class="eyebrow">工作空间 / 情报问答</span><h1>可追溯问答</h1><p>从资料中查找证据，支持追问与来源核对。</p></div>
    <button class="primary-link" type="button" :disabled="sending" @click="reset">新会话</button>
  </div>
  <div class="qa-layout">
    <section class="panel chat-panel" aria-label="情报问答">
      <div class="chat-header">
        <span class="avatar assistant-avatar" aria-hidden="true"><img :src="assistantAvatar" class="chat-avatar-image" alt="" width="36" height="36" /></span>
        <div><strong>安全情报助手</strong><small>后端资料库 · Agent 检索</small></div>
        <span class="chat-online">{{ store.isDemo ? '虚构样本范围' : '真实资料范围' }}</span>
      </div>
      <ChatConversation :messages="messages" :sending="sending" />
      <ChatComposer :disabled="!userToken" :sending="sending" @submit="submit" />
    </section>
    <aside class="qa-aside">
      <section class="panel callout-panel">
        <span class="eyebrow">使用说明</span><h2>有来源的回答</h2>
        <p>可以询问某个 CVE 的影响，或查询资料中记录的 AI 安全风险。资料不足时会明确说明。</p>
        <p>未配置模型时展示来源摘录；启用 Flash 后根据证据生成回答。每条结果会标明模式。</p>
        <div class="aside-divider" /><h3>资料范围</h3>
        <p>{{ store.isDemo ? '当前使用明确标注的虚构测试资料。' : '当前使用后端采集并同步的真实来源资料。' }}</p>
        <template v-if="!userToken"><h3>用户授权</h3><p>填写后端签发的短期用户令牌。</p>
          <input v-model="userToken" type="password" autocomplete="off" aria-label="短期用户令牌" /></template>
      </section>
    </aside>
  </div>
</template>
