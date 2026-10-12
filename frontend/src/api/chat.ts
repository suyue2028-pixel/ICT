import { apiGet } from './http'
import type { ChatMessage, AnswerStatus, AnswerCoverage, RetrievalMode, ResponseKind } from '@/types'

export interface ChatAnswer {
  status: AnswerStatus
  answer: string
  session_id: string
  request_id: string
  generation_mode?: 'live' | 'extractive' | 'mock' | null
  llm_http_calls?: number | null
  partial_errors: string[]
  retrieval_mode: RetrievalMode
  response_kind?: ResponseKind
  coverage?: AnswerCoverage
  workflow?: unknown
  tool_trace?: unknown
  invocation_id?: string
  question_type?: string | null
  requirement_coverage?: unknown
  retrieval_summary?: unknown
  limitations?: unknown
  sources: { document_id: string; title: string; content_hash?: string }[]
  evidence: NonNullable<ChatMessage['evidence']>
}

export interface SessionSummary {
  session_id: string
  is_demo: boolean
  turn_count: number
  provider: string
  updated_at: string
}

export interface SavedSession {
  session_id: string
  is_demo: boolean
  turn_count: number
  messages: {
    id?: string
    role: 'user' | 'assistant'
    content: string
    citations: ChatAnswer['evidence']
    response_kind?: ResponseKind
    retrieval_mode?: RetrievalMode | null
    created_at?: string
    generation_mode?: ChatAnswer['generation_mode']
    llm_http_calls?: number | null
  }[]
}

export interface HistorySession extends SessionSummary {
  title: string
  preview: string
}

export type AnswerMetadata = Pick<ChatMessage, 'generationMode' | 'warnings' | 'citations' | 'responseKind' | 'retrievalMode' | 'status' | 'coverage' | 'workflow' | 'toolTrace' | 'invocationId' | 'requestId'>
export const getChatSessions = (page: number) =>
  apiGet<{ items: SessionSummary[]; total: number; page: number; page_size: number }>(
    '/agent/sessions', { page, page_size: 20 },
  )
export const getChatSession = (id: string) => apiGet<SavedSession>(`/agent/sessions/${id}`)
