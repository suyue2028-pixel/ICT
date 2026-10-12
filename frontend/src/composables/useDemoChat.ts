import { computed, onMounted, shallowRef, watch } from 'vue'
import axios from 'axios'
import { apiGet, ensureSession, http, requestFailure, UserAuthorizationRequired, userToken } from '@/api/http'
import { normalizeWorkflow, normalizeToolTrace, returnedWorkflow } from '@/utils/answerWorkflow'
import { generationModeLabel as modeLabel, responseKind, UNVERIFIED_EXPLANATION_LABEL } from '@/utils/chatPresentation'
import { readFailedQuestion, saveFailedQuestion } from '@/utils/queryDraft'
import { useIntelligenceStore } from '@/stores/intelligence'
import type { AnswerCoverage, AnswerStatus, ChatMessage, IntelligenceItem, ResponseKind, RetrievalMode } from '@/types'
interface Answer {
  status: AnswerStatus; answer: string; session_id: string; request_id: string;
  partial_errors: string[]; retrieval_mode: RetrievalMode; response_kind?: ResponseKind;
  coverage?: AnswerCoverage; workflow?: unknown; tool_trace?: unknown; invocation_id?: string;
  question_type?: string | null; requirement_coverage?: unknown; retrieval_summary?: unknown; limitations?: unknown;
  sources: { document_id: string; title: string }[];
  evidence: NonNullable<ChatMessage['evidence']>;
}
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
export function useDemoChat(getItems: () => IntelligenceItem[]) {
  const store = useIntelligenceStore()
  const messages = shallowRef<ChatMessage[]>([{ id: 'welcome', role: 'assistant', content:
    '你好！可以先聊聊你想了解什么，也可以输入漏洞编号或 AI 安全问题。我会为资料问答保留来源。', time: '', responseKind: 'conversation' }])
  const sending = shallowRef(false), sessionId = shallowRef<string | null>(null)
  const failedQuestion = shallowRef(readFailedQuestion(store.isDemo))
  let sequence = 0, context = 0
  type Metadata = Pick<ChatMessage, 'generationMode' | 'warnings' | 'citations' | 'responseKind' | 'retrievalMode' | 'status' | 'coverage' | 'workflow' | 'toolTrace' | 'invocationId' | 'requestId'>
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
    failedQuestion.value = readFailedQuestion(store.isDemo)
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
        const retrievalMode = row.retrieval_mode || saved?.retrievalMode
        const restoredMode = retrievalMode === 'llm_unverified' ? UNVERIFIED_EXPLANATION_LABEL : kind === 'knowledge' && !row.citations.length && saved?.generationMode?.startsWith('资料摘录')
          ? '历史消息 · 未记录可核验引用' : saved?.generationMode || '历史消息 · 生成模式未记录'
        return { id: row.id || `history-${index}`, role: row.role,
          content: row.content, time: date && Number.isFinite(date.getTime()) ? clock.format(date) : '',
          generationMode: row.role === 'assistant' ? restoredMode : undefined,
          responseKind: row.role === 'assistant' ? kind : undefined,
          retrievalMode: row.role === 'assistant' ? retrievalMode : undefined,
          status: saved?.status,
          coverage: saved?.coverage,
          workflow: normalizeWorkflow(saved?.workflow, row.citations.length),
          toolTrace: normalizeToolTrace(saved?.toolTrace),
          invocationId: saved?.invocationId, requestId: saved?.requestId,
          warnings: saved?.warnings,
          evidence: row.citations, citations: row.citations.filter((item, index, rows) => rows.findIndex(x => x.document_id === item.document_id && x.content_hash === item.content_hash) === index).map(item => ({ intelligenceId: item.document_id,
            contentHash: item.content_hash, label: saved?.citations?.find(x => x.intelligenceId === item.document_id && x.contentHash === item.content_hash)?.label || getItems().find(x => x.id === item.document_id)?.title || '原始来源文档' })) }
      })
      while (metadata.length < assistantIndex) metadata.push({})
    } catch (cause) {
      // A stale credential or transient failure must not discard conversation history.
      if (version === context && axios.isAxiosError(cause) && [403, 404].includes(cause.response?.status || 0)) {
        sessionId.value = null; sessionStorage.removeItem(key)
      }
    }
  }
  function reset() {
    sessionStorage.removeItem(sessionKey())
    clearView()
  }
  watch(() => store.isDemo, () => { clearView(); void restore() })
  onMounted(() => { void restore() })
  function coverage(answer: Answer): AnswerCoverage | undefined {
    if (!answer.coverage) return undefined
    const topics = (value: unknown): string[] => Array.isArray(value)
      ? [...new Set(value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map(item => item.trim()))].slice(0, 12)
      : []
    return { answered_topics: topics(answer.coverage.answered_topics), missing_topics: topics(answer.coverage.missing_topics) }
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
      if (!userToken.value) throw new UserAuthorizationRequired()
      const response = await http.post<Answer>('/agent/query', { query: clean, session_id: previousSession,
        is_demo: scope }, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      const answer = response.data
      sessionStorage.setItem(key, answer.session_id)
      if (version !== context) return
      sessionId.value = answer.session_id
      failedQuestion.value = ''; saveFailedQuestion(scope, '')
      const workflow = returnedWorkflow(answer, answer.evidence.length), toolTrace = normalizeToolTrace(answer.tool_trace)
      remember(answer.session_id, { generationMode: modeLabel(answer, response.headers),
        responseKind: responseKind(answer), retrievalMode: answer.retrieval_mode,
        status: answer.status, coverage: coverage(answer),
        workflow, toolTrace, invocationId: answer.invocation_id, requestId: answer.request_id,
        warnings: answerWarnings(answer),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) })
      messages.value = [...messages.value, { id: `a-${++sequence}`, role: 'assistant', content: answer.answer,
        time: clock.format(new Date()), evidence: answer.evidence,
        generationMode: modeLabel(answer, response.headers),
        responseKind: responseKind(answer), retrievalMode: answer.retrieval_mode,
        status: answer.status, coverage: coverage(answer),
        workflow, toolTrace, invocationId: answer.invocation_id, requestId: answer.request_id,
        warnings: answerWarnings(answer),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: answer.evidence.find(x => x.document_id === source.document_id)?.content_hash, label: source.title })) }]
    } catch (cause) {
      if (version !== context) return
      failedQuestion.value = clean; saveFailedQuestion(scope, clean)
      const failure = requestFailure(cause)
      messages.value = [...messages.value, { id: `error-${++sequence}`, role: 'assistant',
        content: failure.message, failureKind: failure.kind,
        time: clock.format(new Date()), status: 'request_failed', generationMode: failure.heading }]
    } finally { sending.value = false }
  }
  return { messages: computed(() => messages.value), sending: computed(() => sending.value), sessionId, failedQuestion, submit, reset, userToken }
}
