import axios from 'axios'
import { shallowRef } from 'vue'

// Ordinary user credential only, in memory. Never put admin/model keys here.
export const userToken = shallowRef('')
let sessionAttempt: Promise<void> | undefined
export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1', timeout: 40_000 })
export async function ensureSession() {
  if (!sessionAttempt) sessionAttempt = (async () => {
    if (!import.meta.env.DEV) return
    try {
      const response = await fetch('/__local/session', { cache: 'no-store' })
      if (!response.ok) return
      const result = await response.json() as { access_token?: string; expires_at?: string }
      if (result.access_token && Date.parse(result.expires_at || '') > Date.now()) userToken.value = result.access_token
    } catch { /* A deployed app uses its own user sign-in. */ }
  })()
  await sessionAttempt
}
http.interceptors.request.use(config => {
  if (userToken.value) config.headers.Authorization = `Bearer ${userToken.value}`
  return config
})
export function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const code = error.response?.data?.error?.code || error.response?.data?.detail
    if (error.response?.status === 401) return '用户会话未授权或已过期，请重新登录。'
    if (error.response?.status === 504 || ['ECONNABORTED', 'ETIMEDOUT'].includes(error.code || '')) return '回答超时，请缩小问题范围后重试。'
    const explanations: Record<string, string> = {
      model_relevance_failed: '模型服务未能完成资料相关性核验，本次回答中断。这不代表知识库没有相关资料，请稍后重试。',
      model_verification_failed: '模型服务未能完成回答的事实核验，本次未返回未经核实的答案。这不代表知识库没有相关资料，请稍后重试。',
      model_generation_failed: '模型服务未能完成回答生成。这不代表知识库没有相关资料，请稍后重试。',
      backend_index_unavailable: '资料检索索引暂不可用，本次未能完成检索。这不代表知识库没有相关资料，请检查服务状态后重试。',
      backend_index_embedding_signature_mismatch: '资料检索索引与当前向量模型配置不一致，本次未能完成检索。这不代表知识库没有相关资料，请更新索引配置后重试。',
      retrieval_failed: '资料检索服务异常，本次未能完成检索。这不代表知识库没有相关资料，请稍后重试。',
      agent_query_failed: '问答处理异常，本次未能取得完整回答。这不代表知识库没有相关资料，请稍后重试。',
      agent_rate_limited: '问答服务当前请求过多，请稍后重试。',
      user_query_budget_exceeded: '当前用户的问答请求额度已用完，请等待额度恢复后重试。',
      agent_call_budget_exceeded: '本次回答达到调用次数限制，请缩小问题范围后重试。',
    }
    if (typeof code === 'string' && explanations[code]) return explanations[code]
    if (error.response?.status === 429) return '请求过于频繁或额度已用完，请稍后重试。'
    return typeof code === 'string' ? `请求失败：${code}` : '无法连接服务，请检查服务状态后重试。'
  }
  return '请求失败，请重试。'
}
export async function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
  return (await http.get<T>(path, { params })).data
}
export async function apiPost<TResponse, TBody>(path: string, body: TBody) {
  return (await http.post<TResponse>(path, body)).data
}
