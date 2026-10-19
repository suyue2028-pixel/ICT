import { computed, shallowRef } from 'vue'
import { errorMessage } from '@/api/http'
import { getChatSession, getChatSessions } from '@/api/chat'
import type { HistorySession, SavedSession, SessionSummary } from '@/api/chat'
import type { ChatMessage } from '@/types'

export function useChatHistory(getScope: () => boolean) {
  const sessions = shallowRef<HistorySession[]>([])
  const loading = shallowRef(false), error = shallowRef(''), hasMore = shallowRef(true)
  let page = 1, generation = 0

  function describe(row: SessionSummary, messages: { role: string; content: string }[]): HistorySession {
    const question = messages.find(message => message.role === 'user')?.content
    const answer = messages.filter(message => message.role === 'assistant').at(-1)?.content
    return { ...row, title: question?.slice(0, 70) || `历史会话 · ${row.turn_count} 轮`,
      preview: answer?.replace(/\s+/g, ' ').slice(0, 90) || '点击查看完整对话与引用' }
  }
  function upsert(row: HistorySession) {
    const current = sessions.value.find(item => item.session_id === row.session_id)
    if (current && Date.parse(current.updated_at) > Date.parse(row.updated_at)) return
    sessions.value = [...sessions.value.filter(item => item.session_id !== row.session_id), row]
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
  }
  function clear() {
    generation++; page = 1
    sessions.value = []; error.value = ''; loading.value = false; hasMore.value = true
  }
  async function loadMore() {
    if (loading.value || !hasMore.value) return
    const request = generation, scope = getScope()
    loading.value = true; error.value = ''
    try {
      let rows: SessionSummary[] = []
      for (let count = 0; count < 5 && !rows.length && hasMore.value; count++) {
        const result = await getChatSessions(page)
        if (request !== generation) return
        page++; hasMore.value = (page - 1) * result.page_size < result.total
        rows = result.items.filter(row => row.is_demo === scope && row.provider === 'external' && row.turn_count > 0)
      }
      for (const row of rows) upsert(describe(row, []))
      let cursor = 0
      await Promise.all(Array.from({ length: Math.min(4, rows.length) }, async () => {
        while (request === generation && cursor < rows.length) {
          const row = rows[cursor++]!
          try {
            const session = await getChatSession(row.session_id)
            if (request !== generation || session.is_demo !== scope) return
            upsert(describe(row, session.messages))
          } catch { /* Keep the entry so selecting it can explicitly retry. */ }
        }
      }))
    } catch (cause) { if (request === generation) error.value = errorMessage(cause) }
    finally { if (request === generation) loading.value = false }
  }
  async function open(id: string) {
    const request = generation
    const session: SavedSession = await getChatSession(id)
    if (request !== generation || session.is_demo !== getScope()) throw new Error('session_scope_mismatch')
    return session
  }
  function remember(id: string, messages: ChatMessage[], time: string) {
    upsert(describe({ session_id: id, is_demo: getScope(), provider: 'external',
      turn_count: messages.filter(message => message.role === 'user').length, updated_at: time }, messages))
  }
  return { sessions: computed(() => sessions.value), loading: computed(() => loading.value),
    error: computed(() => error.value), hasMore: computed(() => hasMore.value), clear, loadMore, open, remember }
}
