import { computed, shallowRef } from 'vue'
import { createDemoAnswer } from '@/mock/answers'
import type { ChatMessage, IntelligenceItem } from '@/types'

const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })

export function useDemoChat(getItems: () => IntelligenceItem[]) {
  const messages = shallowRef<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        '你好！这里是可追溯问答的前端演示。你可以询问访问控制、智能体工具审计或提示注入等演示情报。',
      time: '',
      simulated: true,
    },
  ])
  const sending = shallowRef(false)
  let sequence = 0

  async function submit(question: string) {
    const clean = question.trim()
    if (!clean || sending.value) return
    messages.value = [
      ...messages.value,
      { id: `u-${++sequence}`, role: 'user', content: clean, time: clock.format(new Date()) },
    ]
    sending.value = true
    try {
      await new Promise((resolve) => setTimeout(resolve, 320))
      const answer = createDemoAnswer(clean, getItems())
      messages.value = [
        ...messages.value,
        {
          id: `a-${++sequence}`,
          role: 'assistant',
          content: answer.content,
          citations: answer.citations,
          simulated: true,
          time: clock.format(new Date()),
        },
      ]
    } finally {
      sending.value = false
    }
  }

  return {
    messages: computed(() => messages.value),
    sending: computed(() => sending.value),
    submit,
  }
}
