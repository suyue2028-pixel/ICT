import { computed, onMounted, shallowRef, watch } from 'vue'
import { apiGet, ensureSession, errorMessage, http, userToken } from '@/api/http'
import { useIntelligenceStore } from '@/stores/intelligence'
import type { ChatMessage, IntelligenceItem } from '@/types'
interface Answer {
  status: string; answer: string; session_id: string; request_id: string;
  partial_errors: string[]; retrieval_mode: string;
  sources: { document_id: string; title: string }[];
  evidence: NonNullable<ChatMessage['evidence']>;
}
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
export function useDemoChat(getItems: () => IntelligenceItem[]) {
  const store = useIntelligenceStore()
  const messages = shallowRef<ChatMessage[]>([{ id: 'welcome', role: 'assistant', content:
    '你好！我会从后端同步的资料中检索证据，并在回答中保留来源。可以输入漏洞编号，或询问 AI 安全风险。', time: '' }])
  const sending = shallowRef(false), sessionId = shallowRef<string | null>(null)
  let sequence = 0, context = 0
  type Metadata = Pick<ChatMessage, 'generationMode' | 'warnings' | 'citations'>
  let metadata: Metadata[] = []
  const metadataKey = (id: string) => `ict:answer-meta:${id}`
  function remember(id: string, row: Metadata) {
    metadata.push(row)
    try { sessionStorage.setItem(metadataKey(id), JSON.stringify(metadata)) } catch { /* Storage can be disabled. */ }
  }
  const sessionKey = () => `ict:session:${store.isDemo ? 'demo' : 'real'}`
  function clearView() {
    context += 1
    sessionId.value = null
    messages.value = [{ id: 'welcome', role: 'assistant', content: '请在当前资料范围输入问题。', time: '' }]
    metadata = []
  }
  async function restore() {
    const version = context, key = sessionKey()
    await ensureSession()
    if (version !== context) return
    const stored = sessionStorage.getItem(key)
    sessionId.value = stored
    if (!stored || !userToken.value) return
    try {
      const result = await apiGet<{ is_demo: boolean; messages: { id?: string; role: 'user' | 'assistant'; content: string;
        citations: Answer['evidence']; created_at?: string }[] }>(`/agent/sessions/${stored}`)
      if (version !== context) return
      if (result.is_demo !== store.isDemo) throw new Error('session_scope_mismatch')
      try { metadata = JSON.parse(sessionStorage.getItem(metadataKey(stored)) || '[]') as Metadata[]
        if (!Array.isArray(metadata)) metadata = [] } catch { metadata = [] }
      let assistantIndex = 0
      if (result.messages.length) messages.value = result.messages.map((row, index) => {
        const date = row.created_at ? new Date(row.created_at) : null
        const saved = row.role === 'assistant' ? metadata[assistantIndex++] : undefined
        return { id: row.id || `history-${index}`, role: row.role,
          content: row.content, time: date && Number.isFinite(date.getTime()) ? clock.format(date) : '',
          generationMode: row.role === 'assistant' ? saved?.generationMode || '历史消息 · 生成模式未记录' : undefined,
          warnings: saved?.warnings,
          evidence: row.citations, citations: row.citations.filter((item, index, rows) => rows.findIndex(x => x.document_id === item.document_id && x.content_hash === item.content_hash) === index).map(item => ({ intelligenceId: item.document_id,
            contentHash: item.content_hash, label: saved?.citations?.find(x => x.intelligenceId === item.document_id && x.contentHash === item.content_hash)?.label || getItems().find(x => x.id === item.document_id)?.title || '原始来源文档' })) }
      })
      while (metadata.length < assistantIndex) metadata.push({})
    } catch {
      if (version === context) { sessionId.value = null; sessionStorage.removeItem(key) }
    }
  }
  function reset() {
    sessionStorage.removeItem(sessionKey())
    clearView()
  }
  watch(() => store.isDemo, () => { clearView(); void restore() })
  onMounted(() => { void restore() })
  function modeLabel(answer: Answer, headers: Record<string, unknown>) {
    const notes = answer.partial_errors
    if (notes.some(x => x.includes('after_remote_attempt'))) return '资料摘录 · 大模型调用后降级'
    if (notes.some(x => x.includes('after_mock_attempt'))) return '资料摘录 · 测试替身调用后降级'
    if (notes.some(x => x.includes('mock_not_real'))) return '测试替身结果'
    if (headers['x-rag-answer-mode'] === 'extractive' || notes.some(x => x.includes('generation_mode:extractive'))) {
      return Number(headers['x-rag-llm-calls'] || 0) > 0 ? '资料摘录 · 大模型调用后降级' : '资料摘录 · 未调用大模型生成'
    }
    return headers['x-rag-answer-mode'] === 'live' ? '大模型回答' : '回答 · 生成模式未提供'
  }
  async function submit(question: string) {
    const clean = question.trim()
    if (!clean || sending.value) return
    const version = context, scope = store.isDemo, key = sessionKey(), previousSession = sessionId.value
    sending.value = true
    messages.value = [...messages.value, { id: `u-${++sequence}`, role: 'user', content: clean, time: clock.format(new Date()) }]
    try {
      await ensureSession()
      if (version !== context) return
      if (!userToken.value) throw new Error('user_not_authorized')
      const response = await http.post<Answer>('/agent/query', { query: clean, session_id: previousSession,
        is_demo: scope }, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      const answer = response.data
      sessionStorage.setItem(key, answer.session_id)
      if (version !== context) return
      sessionId.value = answer.session_id
      remember(answer.session_id, { generationMode: modeLabel(answer, response.headers),
        warnings: answer.partial_errors.filter(x => !x.startsWith('generation_mode:')),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) })
      messages.value = [...messages.value, { id: `a-${++sequence}`, role: 'assistant', content: answer.answer,
        time: clock.format(new Date()), evidence: answer.evidence, requestId: answer.request_id,
        generationMode: modeLabel(answer, response.headers),
        warnings: answer.partial_errors.filter(x => !x.startsWith('generation_mode:')),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) }]
    } catch (cause) {
      if (version !== context) return
      messages.value = [...messages.value, { id: `error-${++sequence}`, role: 'assistant',
        content: userToken.value ? errorMessage(cause) : '尚未获得用户会话，请填写短期用户令牌或重新启动本地服务。',
        time: clock.format(new Date()), generationMode: '请求失败' }]
    } finally { sending.value = false }
  }
  return { messages: computed(() => messages.value), sending: computed(() => sending.value), sessionId, submit, reset, userToken }
}
