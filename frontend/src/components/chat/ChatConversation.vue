<script setup lang="ts">
import { nextTick, useTemplateRef, watch } from 'vue'
import assistantAvatar from '@/assets/assistant-avatar.svg'
import type { ChatMessage } from '@/types'

const props = defineProps<{ messages: ChatMessage[]; sending: boolean }>()
const feed = useTemplateRef<HTMLElement>('feed')
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
    aria-label="演示对话"
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
        <div class="message-bubble">{{ message.content }}</div>
        <div v-if="message.citations?.length" class="citation-box">
          <span>引用的演示情报</span
          ><router-link
            v-for="citation in message.citations"
            :key="citation.intelligenceId"
            :to="`/intelligence/${citation.intelligenceId}`"
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
    <div v-if="sending" class="thinking" role="status">正在生成本地模拟回答…</div>
  </div>
</template>
