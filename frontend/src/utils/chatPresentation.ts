import type { ChatMessage, ResponseKind, RetrievalMode } from '../types/index'

export const UNVERIFIED_EXPLANATION_LABEL = '模型一般解释 · 未经实时来源核验'
export const searchWarningLabels: Record<string, string> = {
  search_not_configured: '联网搜索服务尚未配置，本次未取得联网来源；这不等于知识问题无法解释',
  search_auth_failed: '平台搜索服务认证失败，本次联网检索未完成；用户登录状态未因此失效',
  search_rate_limited: '联网搜索服务限流，本次未完成补充检索',
  search_timeout: '联网搜索服务响应超时，本次未完成补充检索',
  search_source_unavailable: '联网搜索服务暂不可用，本次未完成补充检索',
  search_invalid_response: '联网搜索返回格式未通过校验，相关结果未作为证据',
  search_unsafe_url: '联网结果包含不安全地址，相关结果已阻止使用',
  search_response_too_large: '联网搜索响应超过长度限制，相关结果未作为证据',
  search_busy: '联网搜索服务当前繁忙，本次未完成补充检索',
}
const warningLabels: Record<string, string> = {
  ...searchWarningLabels,
  answer_rebuilt_after_citation_validation: '部分引用未通过核对，已改为有效来源摘录',
  citation_excerpt_not_in_backend_revision: '部分引用无法在原始版本中定位',
  source_fields_missing: '来源没有记录部分结构化字段',
  source_assertions_conflict: '不同来源的字段存在分歧',
  tool_trace_incomplete: '部分检索过程记录不完整',
  answer_budget_truncated: '回答达到长度限制，可缩小问题范围后追问',
  transport_answer_truncated: '回答达到传输长度限制',
  transport_evidence_truncated: '部分来源摘录超过展示长度，相关生成结论已撤下',
  requested_field_omitted_due_to_size: '部分请求字段超过引用长度限制，请核对原始资料',
  transport_answer_limit: '回答达到长度限制',
  coverage_incomplete: '现有证据仅支持问题的一部分',
  local_retrieval_failed: '本地资料检索服务异常，不能据此判断知识库没有相关内容',
  retrieval_failed: '资料检索服务异常，不能据此判断知识库没有相关内容',
  model_classification_failed: '模型未能完成问题理解与分类，本次请求未进入正常问答流程',
  model_conversation_failed: '模型未能完成日常回复，请稍后重试',
  model_generation_failed: '大模型生成失败，当前仅显示已取得的资料证据；这不代表知识库没有相关资料',
  relevance_check_failed: '大模型相关性核验失败，回答未完成；这不代表知识库没有相关资料',
  claim_support_check_failed: '大模型事实核验失败，回答未完成；这不代表知识库没有相关资料',
  llm_unverified: '本条是模型一般解释，尚未由本次来源证据核验',
}
export function warningCode(note: string): string {
  return note.split(':').find(code => Object.hasOwn(warningLabels, code)) || note.split(':')[0] || ''
}
export function warningText(warnings: string[]): string {
  return [...new Set(warnings.filter(code => !code.startsWith('relevance_filter:') && !code.startsWith('generation_mode:'))
    .map(code => warningLabels[warningCode(code)] || '部分信息未完整返回'))].join('；')
}
export function isUnverifiedMessage(message: Pick<ChatMessage, 'retrievalMode' | 'responseKind'>): boolean {
  return message.responseKind !== 'conversation' && message.retrievalMode === 'llm_unverified'
}
export function showSecurityEvidence(message: Pick<ChatMessage, 'retrievalMode' | 'responseKind'>): boolean {
  return message.responseKind !== 'conversation' && !isUnverifiedMessage(message)
}
export function messageHeading(message: ChatMessage): string {
  if (message.role === 'user') return '你的问题'
  if (message.status === 'request_failed') return '请求异常'
  if (message.responseKind === 'conversation') return '基础对话'
  if (isUnverifiedMessage(message)) return '模型一般解释'
  if (!message.evidence?.length) {
    const codes = (message.warnings || []).map(warningCode)
    if (codes.some(code => Object.hasOwn(searchWarningLabels, code))) return codes.includes('search_not_configured') ? '联网检索未配置' : '联网检索未完成'
    if (codes.includes('local_retrieval_failed') || codes.includes('retrieval_failed')) return '本地检索异常'
    if (codes.some(code => ['model_classification_failed', 'model_generation_failed', 'relevance_check_failed', 'claim_support_check_failed'].includes(code))) return '模型服务异常'
  }
  if (message.status === 'insufficient_evidence') return '资料不足'
  return message.status === 'partial' ? '部分资料回答' : '资料回答'
}
export function messageGenerationLabel(message: ChatMessage): string {
  if (!isUnverifiedMessage(message)) return message.generationMode || ''
  return message.generationMode?.startsWith(UNVERIFIED_EXPLANATION_LABEL) ? message.generationMode : UNVERIFIED_EXPLANATION_LABEL
}
export function toolLabel(name: string): string { return name === 'web_search' ? '联网检索' : name }

export interface ModeInput {
  status: string
  partial_errors: string[]
  retrieval_mode: RetrievalMode
  response_kind?: ResponseKind
  evidence: unknown[]
}
export function responseKind(answer: ModeInput): ResponseKind {
  return answer.response_kind || (answer.retrieval_mode === 'conversation' ? 'conversation' : 'knowledge')
}
export function generationModeLabel(answer: ModeInput, headers: Record<string, unknown>): string {
  const notes = answer.partial_errors, conversation = responseKind(answer) === 'conversation'
  const hasCode = (code: string) => notes.some(note => warningCode(note) === code)
  const hasEvidence = answer.evidence.length > 0
  if (!conversation && answer.retrieval_mode === 'llm_unverified') {
    return UNVERIFIED_EXPLANATION_LABEL + (notes.some(note => note.includes('mock_not_real')) ? ' · 测试替身结果' : '')
  }
  if (!conversation) {
    if (['model_classification_failed', 'model_generation_failed', 'relevance_check_failed', 'claim_support_check_failed'].some(hasCode)) {
      return hasEvidence ? '模型服务异常 · 已显示资料证据' : '模型服务异常 · 未生成回答'
    }
    if (hasCode('retrieval_failed') || hasCode('local_retrieval_failed')) return hasEvidence ? '本地检索服务异常 · 仅显示已取得的证据' : '本地检索服务异常 · 未取得证据'
    if (!hasEvidence && notes.some(note => Object.hasOwn(searchWarningLabels, warningCode(note)))) {
      return hasCode('search_not_configured') ? '联网检索未配置 · 未取得可核验来源' : '联网检索未完成 · 未取得可核验来源'
    }
    if (answer.status === 'insufficient_evidence' || !hasEvidence) return '证据不足 · 未生成资料回答'
  }
  if (notes.some(note => note.includes('after_remote_attempt'))) return conversation ? '模型暂不可用 · 本地回复' : '资料摘录 · 大模型调用后降级'
  if (notes.some(note => note.includes('after_mock_attempt'))) return conversation ? '本地回复 · 测试替身调用后降级' : '资料摘录 · 测试替身调用后降级'
  if (notes.some(note => note.includes('mock_not_real'))) return '测试替身结果'
  if (headers['x-rag-answer-mode'] === 'extractive' || notes.some(note => note.includes('generation_mode:extractive'))) {
    if (conversation) return Number(headers['x-rag-llm-calls'] || 0) > 0 ? '模型暂不可用 · 本地回复' : '本地回复 · 未调用大模型'
    return Number(headers['x-rag-llm-calls'] || 0) > 0 ? '资料摘录 · 大模型调用后降级' : '资料摘录 · 未调用大模型生成'
  }
  return headers['x-rag-answer-mode'] === 'live' ? '大模型回答' : '回答 · 生成模式未提供'
}
