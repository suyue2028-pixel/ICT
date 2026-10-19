/** Classify public error codes. An upstream 401 is not a user sign-in failure. */
import { searchWarningLabels } from './chatPresentation.ts'
export interface RequestFailureInput { status?: number; code?: string; data?: unknown }
export type FailureKind = 'user_auth' | 'model' | 'search' | 'retrieval' | 'timeout' | 'rate_limit' | 'service' | 'network'
const userCodes = new Set(['user_token_required', 'invalid_user_token', 'user_session_expired', 'user_session_revoked'])
export function publicErrorCode(data: unknown): string {
  if (!data || typeof data !== 'object') return ''
  const row = data as Record<string, unknown>, error = row.error
  if (error && typeof error === 'object' && typeof (error as Record<string, unknown>).code === 'string') return (error as Record<string, unknown>).code as string
  if (typeof row.detail === 'string') return row.detail
  if (row.detail && typeof row.detail === 'object' && typeof (row.detail as Record<string, unknown>).code === 'string') return (row.detail as Record<string, unknown>).code as string
  return ''
}
export function isUserAuthorizationFailure(input: RequestFailureInput): boolean {
  return input.status === 401 && userCodes.has(publicErrorCode(input.data))
}
export function shouldInvalidateCredential(sent: unknown, current: string, input: RequestFailureInput): boolean {
  return Boolean(current) && String(sent || '') === `Bearer ${current}` && isUserAuthorizationFailure(input)
}
export function knownSessionExpired(expiresAt: string | null, now = Date.now()): boolean {
  return Boolean(expiresAt) && (!Number.isFinite(Date.parse(expiresAt || '')) || Date.parse(expiresAt || '') <= now)
}
export function isVerifiedUserIdentity(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const row = value as Record<string, unknown>
  return row.authenticated === true && typeof row.owner_id === 'string' && Boolean(row.owner_id.trim())
}
const messages: Record<string, string> = {
  model_relevance_failed: '模型服务未能完成资料相关性核验，本次回答中断。这不代表知识库没有相关资料，请稍后重试。',
  model_verification_failed: '模型服务未能完成回答的事实核验，本次未返回未经核实的答案，请稍后重试。',
  model_generation_failed: '模型服务未能完成回答生成，请稍后重试。',
  model_classification_failed: '模型未能完成问题理解与分类，本次请求未进入正常问答流程，请稍后重试。这不代表知识库没有资料。',
  model_conversation_failed: '模型未能完成日常回复，请稍后重试。',
  model_auth_failed: '平台模型服务认证失败，请联系平台管理员检查服务器端配置。你的用户登录状态未因此失效。',
  llm_auth_failed: '平台模型服务认证失败，请联系平台管理员检查服务器端配置。你的用户登录状态未因此失效。',
  deepseek_auth_failed: '平台模型服务认证失败，请联系平台管理员检查服务器端配置。你的用户登录状态未因此失效。',
  platform_model_auth_failed: '平台模型服务认证失败，请联系平台管理员检查服务器端配置。你的用户登录状态未因此失效。',
  platform_model_rate_limited: '平台模型服务限流，请稍后重试。',
  platform_model_timeout: '平台模型服务响应超时，请稍后重试或缩小问题范围。',
  platform_model_unavailable: '平台模型服务暂不可用，请稍后重试。',
  platform_model_format_invalid: '平台模型返回的格式未通过校验，本次回答中断，请稍后重试。',
  platform_model_capability_unsupported: '当前模型不支持本次工作流所需的能力，请联系平台管理员检查模型配置。',
  platform_model_not_configured: '平台默认模型尚未配置，请联系平台管理员；普通用户无需填写模型 API Key。',
  model_rate_limited: '平台模型服务限流，请稍后重试。',
  llm_rate_limited: '平台模型服务限流，请稍后重试。',
  model_timeout: '平台模型服务响应超时，请稍后重试或缩小问题范围。',
  llm_timeout: '平台模型服务响应超时，请稍后重试或缩小问题范围。',
  model_capability_unsupported: '当前模型不支持本次工作流所需的能力，请联系平台管理员检查模型配置。',
  backend_index_unavailable: '资料检索索引暂不可用，这不代表知识库没有相关资料，请稍后重试。',
  backend_index_embedding_signature_mismatch: '资料索引与向量模型配置不一致，请联系平台管理员检查索引配置。',
  retrieval_failed: '资料检索服务异常，这不代表知识库没有相关资料，请稍后重试。',
  local_retrieval_failed: '本地资料检索服务异常，这不代表知识库没有相关资料，请稍后重试。',
  agent_query_failed: '问答处理异常，本次未能取得完整回答，请稍后重试。',
  agent_rate_limited: '问答服务当前请求过多，请稍后重试。',
  user_query_budget_exceeded: '当前用户的问答请求额度已用完，请等待额度恢复后重试。',
  agent_call_budget_exceeded: '本次回答达到调用次数限制，请缩小问题范围后重试。',
}
export function classifyFailure(input: RequestFailureInput): { kind: FailureKind; heading: string; message: string } {
  const code = publicErrorCode(input.data)
  if (isUserAuthorizationFailure(input)) return { kind: 'user_auth', heading: '用户授权已失效', message: '用户会话未授权或已过期。问题已保留，请重新授权后重试。' }
  if (/^(platform_model_|model_|llm_|deepseek_)/.test(code)) return { kind: 'model', heading: '平台模型服务异常', message: messages[code] || '平台模型服务暂不可用，请稍后重试。你的用户登录状态未因此失效。' }
  if (code.startsWith('search_')) return { kind: 'search', heading: code === 'search_not_configured' ? '联网搜索未配置' : '联网搜索服务异常',
    message: Object.hasOwn(searchWarningLabels, code) ? `${searchWarningLabels[code]}。${code === 'search_not_configured' ? '请联系平台管理员配置搜索服务。' : '请稍后重试或联系平台管理员。'}` : '联网搜索未完成，请稍后重试；这不代表本地知识库没有资料。' }
  if (['backend_index_unavailable', 'backend_index_embedding_signature_mismatch', 'retrieval_failed', 'local_retrieval_failed'].includes(code)) return { kind: 'retrieval', heading: '检索服务异常', message: messages[code] || '资料检索服务异常，请稍后重试。' }
  if (input.status === 504 || ['ECONNABORTED', 'ETIMEDOUT'].includes(input.code || '')) return { kind: 'timeout', heading: '回答超时', message: '回答超时，问题已保留，可以缩小问题范围后重试。' }
  if (input.status === 429) return { kind: 'rate_limit', heading: '请求受限', message: messages[code] || '请求过于频繁或额度已用完，请稍后重试。' }
  if (input.status === 401) return { kind: 'service', heading: '服务认证异常', message: '服务返回认证异常，但未标明为用户会话失效。请联系平台管理员检查服务配置。' }
  if (!input.status) return { kind: 'network', heading: '连接异常', message: '无法连接服务，问题已保留，请检查服务状态后重试。' }
  return { kind: 'service', heading: '问答服务异常', message: messages[code] || '服务请求失败，问题已保留，请稍后重试。' }
}
/** Only an internal path can be used after sign-in; no external redirect. */
export function safeReturnPath(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') && !value.startsWith('/login') ? value : '/qa'
}
