import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { ensureSession, errorMessage, http, userToken, requestFailure, UserAuthorizationRequired } from '@/api/http'
import { normalizeWorkflow, normalizeToolTrace, returnedWorkflow } from '@/utils/answerWorkflow'
import { generationModeLabel, responseKind, UNVERIFIED_EXPLANATION_LABEL } from '@/utils/chatPresentation'
import { readFailedQuestion, saveFailedQuestion } from '@/utils/queryDraft'
import { useIntelligenceStore } from '@/stores/intelligence'
import { useChatHistory } from './useChatHistory'
import { chatStorageKey, savedMetadata, savedSelection, saveMetadata, saveSelection } from '@/utils/chatHistoryStorage'
import type { AnswerMetadata, ChatAnswer, SavedSession } from '@/api/chat'
import type { ChatMessage, IntelligenceItem } from '@/types'

const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
const welcome = (): ChatMessage[] => [{ id: 'welcome', role: 'assistant', content:
  '你好！输入漏洞编号或安全问题，我会帮你查找资料并保留来源。也可以从历史会话继续之前的对话。', time: '', responseKind: 'conversation' }]

export function useDemoChat(getItems: () => IntelligenceItem[]) {
  const store = useIntelligenceStore()
  const failedQuestion = shallowRef(readFailedQuestion(store.isDemo))
  const history = useChatHistory(() => store.isDemo)
  const messages = shallowRef<ChatMessage[]>(welcome())
  const sending = shallowRef(false), restoring = shallowRef(false), conversationError = shallowRef('')
  const ready = shallowRef(false)
  const sessionId = shallowRef<string | null>(null)
  let context = 0, storageKey = '', metadata: AnswerMetadata[] = []
  const legacyKey = () => `ict:session:${store.isDemo ? 'demo' : 'real'}`

  function modeLabel(mode?: string | null, calls?: number | null) {
    if (mode === 'mock') return '测试替身结果'
    if (mode === 'extractive') return calls && calls > 0 ? '资料摘录 · 大模型调用后降级' : '资料摘录 · 未调用大模型生成'
    if (mode === 'live') return '大模型回答'
    return '历史消息 · 生成模式未记录'
  }

  function restoreMessages(session: SavedSession) {
    let assistantIndex = 0
    return session.messages.map((row, index): ChatMessage => {
      const date = row.created_at ? new Date(row.created_at) : null
      const saved = row.role === 'assistant' ? metadata[assistantIndex++] : undefined
      const evidence = row.citations || []
      const kind = row.response_kind || (row.retrieval_mode === 'conversation' ? 'conversation' : saved?.responseKind || 'knowledge')
      const retrievalMode = row.retrieval_mode || saved?.retrievalMode
      const restoredMode = retrievalMode === 'llm_unverified' ? UNVERIFIED_EXPLANATION_LABEL
        : kind === 'knowledge' && !evidence.length && saved?.generationMode?.startsWith('资料摘录')
          ? '历史消息 · 未记录可核验引用'
          : row.generation_mode ? modeLabel(row.generation_mode, row.llm_http_calls) : saved?.generationMode || modeLabel()
      return { id: row.id || `history-${index}`, role: row.role, content: row.content, persisted: true,
        time: date && Number.isFinite(date.getTime()) ? clock.format(date) : '',
        generationMode: row.role === 'assistant' ? restoredMode : undefined,
        responseKind: row.role === 'assistant' ? kind : undefined,
        retrievalMode: row.role === 'assistant' ? retrievalMode : undefined,
        status: saved?.status, coverage: saved?.coverage,
        workflow: normalizeWorkflow(saved?.workflow, evidence.length), toolTrace: normalizeToolTrace(saved?.toolTrace),
        invocationId: saved?.invocationId, requestId: saved?.requestId,
        warnings: saved?.warnings, evidence,
        citations: evidence.filter((item, i, rows) => rows.findIndex(other => other.document_id === item.document_id
          && other.content_hash === item.content_hash) === i).map(item => ({ intelligenceId: item.document_id,
          contentHash: item.content_hash, label: saved?.citations?.find(citation => citation.intelligenceId === item.document_id
            && citation.contentHash === item.content_hash)?.label || getItems().find(doc => doc.id === item.document_id)?.title || '原始来源文档' })) }
    })
  }

  async function openSession(id: string) {
    if (sending.value) return
    const version = ++context
    restoring.value = true; conversationError.value = ''
    try {
      const session = await history.open(id)
      if (version !== context) return
      metadata = savedMetadata(storageKey, id)
      messages.value = restoreMessages(session)
      while (metadata.length < session.messages.filter(row => row.role === 'assistant').length) metadata.push({})
      sessionId.value = id; saveSelection(storageKey, id)
    } catch (cause) {
      if (version === context) conversationError.value = `无法打开这段历史会话。${errorMessage(cause)}`
    } finally { if (version === context) restoring.value = false }
  }

  function reset() {
    if (sending.value || restoring.value) return
    context++; sessionId.value = null; messages.value = welcome(); metadata = []; conversationError.value = ''
    if (storageKey) saveSelection(storageKey, null)
    try { sessionStorage.removeItem(legacyKey()) } catch { /* Optional selection cache. */ }
  }

  watch([() => store.isDemo, userToken], async ([scope, token]) => {
    const version = ++context
    failedQuestion.value = readFailedQuestion(scope)
    messages.value = welcome(); sessionId.value = null; metadata = []; storageKey = ''
    sending.value = false; restoring.value = false; ready.value = false; conversationError.value = ''; history.clear()
    if (!token) { await ensureSession(); return }
    try {
      const key = await chatStorageKey(token, scope)
      if (version !== context) return
      storageKey = key
      ready.value = true
      void history.loadMore()
      let selected = savedSelection(key)
      if (selected === null) {
        try { selected = sessionStorage.getItem(legacyKey()) } catch { /* No legacy selection. */ }
      }
      if (selected && selected !== 'new') await openSession(selected)
    } catch (cause) { if (version === context) conversationError.value = errorMessage(cause) }
  }, { immediate: true })

  onBeforeUnmount(() => { context++; history.clear() })

  function answerMode(answer: ChatAnswer, headers: Record<string, unknown>) {
    return generationModeLabel(answer, { ...headers,
      'x-rag-answer-mode': answer.generation_mode ?? headers['x-rag-answer-mode'],
      'x-rag-llm-calls': answer.llm_http_calls ?? headers['x-rag-llm-calls'],
    })
  }
  function coverage(answer: ChatAnswer) {
    if (!answer.coverage) return undefined
    const topics = (value: unknown): string[] => Array.isArray(value)
      ? [...new Set(value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map(item => item.trim()))].slice(0, 12) : []
    return { answered_topics: topics(answer.coverage.answered_topics), missing_topics: topics(answer.coverage.missing_topics) }
  }

  async function submit(question: string) {
    const clean = question.trim()
    if (!clean || sending.value || restoring.value || !storageKey) return
    const version = context, scope = store.isDemo, questionId = crypto.randomUUID()
    saveFailedQuestion(scope, clean)
    sending.value = true; conversationError.value = ''
    messages.value = [...messages.value.filter(row => row.id !== 'welcome'), {
      id: questionId, role: 'user', content: clean, time: clock.format(new Date()), persisted: false,
    }]
    try {
      await ensureSession()
      if (version !== context) return
      if (!userToken.value) throw new UserAuthorizationRequired()
      const response = await http.post<ChatAnswer>('/agent/query', { query: clean, session_id: sessionId.value,
        is_demo: scope }, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      if (version !== context) return
      const answer = response.data
      failedQuestion.value = ''; saveFailedQuestion(scope, '')
      sessionId.value = answer.session_id
      const presentation: AnswerMetadata = { generationMode: answerMode(answer, response.headers),
        responseKind: responseKind(answer), retrievalMode: answer.retrieval_mode,
        status: answer.status, coverage: coverage(answer),
        workflow: returnedWorkflow(answer, answer.evidence.length), toolTrace: normalizeToolTrace(answer.tool_trace),
        invocationId: answer.invocation_id, requestId: answer.request_id,
        warnings: answer.partial_errors.filter(note => !['generation_mode:', 'conversation_local_reply:', 'relevance_filter:'].some(prefix => note.startsWith(prefix))),
        citations: answer.sources.map(source => ({ intelligenceId: source.document_id,
          contentHash: source.content_hash || answer.evidence.find(row => row.document_id === source.document_id)?.content_hash,
          label: source.title })) }
      metadata.push(presentation)
      saveMetadata(storageKey, answer.session_id, metadata); saveSelection(storageKey, answer.session_id)
      messages.value = [...messages.value.map(row => row.id === questionId ? { ...row, persisted: true } : row), {
        id: crypto.randomUUID(), role: 'assistant', content: answer.answer, persisted: true,
        time: clock.format(new Date()), evidence: answer.evidence, requestId: answer.request_id, ...presentation,
      }]
      history.remember(answer.session_id, messages.value.filter(row => row.persisted), new Date().toISOString())
    } catch (cause) {
      if (scope === store.isDemo) failedQuestion.value = readFailedQuestion(scope)
      if (version !== context) return
      const failure = requestFailure(cause)
      messages.value = [...messages.value, { id: crypto.randomUUID(), role: 'assistant',
        content: failure.message, failureKind: failure.kind, status: 'request_failed',
        time: clock.format(new Date()), generationMode: failure.heading, persisted: false }]
    } finally { if (version === context) sending.value = false }
  }

  return { messages: computed(() => messages.value), sending: computed(() => sending.value),
    ready: computed(() => ready.value),
    restoring: computed(() => restoring.value), conversationError: computed(() => conversationError.value),
    sessionId: computed(() => sessionId.value), history, failedQuestion, submit, reset, openSession, userToken }
}
