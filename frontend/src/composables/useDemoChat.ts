import { computed, onMounted, shallowRef, watch } from 'vue'
import axios from 'axios'
import { apiGet, ensureSession, errorMessage, http, userToken } from '@/api/http'
import { useIntelligenceStore } from '@/stores/intelligence'
import type { AnswerCoverage, AnswerStatus, ChatMessage, IntelligenceItem, ResponseKind, RetrievalMode } from '@/types'
interface Answer {
  status: AnswerStatus; answer: string; session_id: string; request_id: string;
  partial_errors: string[]; retrieval_mode: RetrievalMode; response_kind?: ResponseKind;
  coverage?: AnswerCoverage;
  sources: { document_id: string; title: string }[];
  evidence: NonNullable<ChatMessage['evidence']>;
}
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
export function useDemoChat(getItems: () => IntelligenceItem[]) {
  const store = useIntelligenceStore()
  const messages = shallowRef<ChatMessage[]>([{ id: 'welcome', role: 'assistant', content:
    '你好！可以先聊聊你想了解什么，也可以输入漏洞编号或 AI 安全问题。我会为资料问答保留来源。', time: '', responseKind: 'conversation' }])
  const sending = shallowRef(false), sessionId = shallowRef<string | null>(null)
  let sequence = 0, context = 0
  type Metadata = Pick<ChatMessage, 'generationMode' | 'warnings' | 'citations' | 'responseKind' | 'status' | 'coverage'>
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
    messages.value = [{ id: 'welcome', role: 'assistant', content: '你好！可以直接交流，也可以在当前资料范围输入安全问题。', time: '', responseKind: 'conversation' }]
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
        citations: Answer['evidence']; retrieval_mode?: RetrievalMode | null; response_kind?: ResponseKind; created_at?: string }[] }>(`/agent/sessions/${stored}`)
      if (version !== context) return
      if (result.is_demo !== store.isDemo) throw new Error('session_scope_mismatch')
      try { metadata = JSON.parse(sessionStorage.getItem(metadataKey(stored)) || '[]') as Metadata[]
        if (!Array.isArray(metadata)) metadata = [] } catch { metadata = [] }
      let assistantIndex = 0
      if (result.messages.length) messages.value = result.messages.map((row, index) => {
        const date = row.created_at ? new Date(row.created_at) : null
        const saved = row.role === 'assistant' ? metadata[assistantIndex++] : undefined
        const kind = row.response_kind || (row.retrieval_mode === 'conversation' ? 'conversation' : saved?.responseKind || 'knowledge')
        const restoredMode = kind === 'knowledge' && !row.citations.length && saved?.generationMode?.startsWith('资料摘录')
          ? '历史消息 · 未记录可核验引用' : saved?.generationMode || '历史消息 · 生成模式未记录'
        return { id: row.id || `history-${index}`, role: row.role,
          content: row.content, time: date && Number.isFinite(date.getTime()) ? clock.format(date) : '',
          generationMode: row.role === 'assistant' ? restoredMode : undefined,
          responseKind: row.role === 'assistant' ? kind : undefined,
          status: saved?.status,
          coverage: saved?.coverage,
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
  function responseKind(answer: Answer): ResponseKind {
    return answer.response_kind || (answer.retrieval_mode === 'conversation' ? 'conversation' : 'knowledge')
  }
  function modeLabel(answer: Answer, headers: Record<string, unknown>) {
    const notes = answer.partial_errors
    const conversation = responseKind(answer) === 'conversation'
    const hasCode = (code: string) => notes.some(note => note === code || note.startsWith(`${code}:`))
    const hasEvidence = answer.evidence.length > 0
    if (!conversation) {
      if (['model_generation_failed', 'relevance_check_failed', 'claim_support_check_failed'].some(hasCode)) {
        return hasEvidence ? '模型服务异常 · 已显示资料证据' : '模型服务异常 · 未生成回答'
      }
      if (hasCode('retrieval_failed')) return hasEvidence ? '检索服务异常 · 仅显示已取得的证据' : '检索服务异常 · 未取得证据'
      if (answer.status === 'insufficient_evidence' || !hasEvidence) return '证据不足 · 未生成资料回答'
    }
    if (notes.some(x => x.includes('after_remote_attempt'))) return conversation ? '模型暂不可用 · 本地回复' : '资料摘录 · 大模型调用后降级'
    if (notes.some(x => x.includes('after_mock_attempt'))) return conversation ? '本地回复 · 测试替身调用后降级' : '资料摘录 · 测试替身调用后降级'
    if (notes.some(x => x.includes('mock_not_real'))) return '测试替身结果'
    if (headers['x-rag-answer-mode'] === 'extractive' || notes.some(x => x.includes('generation_mode:extractive'))) {
      if (conversation) return Number(headers['x-rag-llm-calls'] || 0) > 0 ? '模型暂不可用 · 本地回复' : '本地回复 · 未调用大模型'
      return Number(headers['x-rag-llm-calls'] || 0) > 0 ? '资料摘录 · 大模型调用后降级' : '资料摘录 · 未调用大模型生成'
    }
    return headers['x-rag-answer-mode'] === 'live' ? '大模型回答' : '回答 · 生成模式未提供'
  }
  function coverage(answer: Answer): AnswerCoverage | undefined {
    if (!answer.coverage) return undefined
    const topics = (value: unknown): string[] => Array.isArray(value)
      ? [...new Set(value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map(item => item.trim()))].slice(0, 12)
      : []
    return { answered_topics: topics(answer.coverage.answered_topics), missing_topics: topics(answer.coverage.missing_topics) }
  }
  function failureLabel(cause: unknown) {
    if (!axios.isAxiosError(cause)) return '请求失败'
    const detail = cause.response?.data?.error?.code || cause.response?.data?.detail
    if (['model_relevance_failed', 'model_verification_failed', 'model_generation_failed'].includes(detail)) return '模型服务异常 · 未生成回答'
    if (['backend_index_unavailable', 'backend_index_embedding_signature_mismatch', 'retrieval_failed'].includes(detail)) return '检索服务异常 · 未取得证据'
    if (cause.response?.status === 429 || detail === 'agent_rate_limited') return '请求受限 · 请稍后重试'
    if (cause.response?.status === 504 || ['ECONNABORTED', 'ETIMEDOUT'].includes(cause.code || '')) return '回答超时'
    if (cause.response?.status === 401) return '用户会话未授权或已过期'
    if ([502, 503].includes(cause.response?.status || 0)) return '问答服务异常 · 未取得回答'
    return '请求失败'
  }
  function answerWarnings(answer: Answer) {
    return answer.partial_errors.filter(x => !x.startsWith('generation_mode:') && !x.startsWith('conversation_local_reply:') && !x.startsWith('relevance_filter:'))
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
        responseKind: responseKind(answer),
        status: answer.status, coverage: coverage(answer),
        warnings: answerWarnings(answer),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) })
      messages.value = [...messages.value, { id: `a-${++sequence}`, role: 'assistant', content: answer.answer,
        time: clock.format(new Date()), evidence: answer.evidence, requestId: answer.request_id,
        generationMode: modeLabel(answer, response.headers),
        responseKind: responseKind(answer),
        status: answer.status, coverage: coverage(answer),
        warnings: answerWarnings(answer),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) }]
    } catch (cause) {
      if (version !== context) return
      messages.value = [...messages.value, { id: `error-${++sequence}`, role: 'assistant',
        content: userToken.value ? errorMessage(cause) : '尚未获得用户会话，请填写短期用户令牌或重新启动本地服务。',
        time: clock.format(new Date()), status: 'request_failed', generationMode: failureLabel(cause) }]
    } finally { sending.value = false }
  }
  return { messages: computed(() => messages.value), sending: computed(() => sending.value), sessionId, submit, reset, userToken }
}
