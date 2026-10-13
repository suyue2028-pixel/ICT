import axios from 'axios'
import { http } from './http'

/** Validate a regular user credential. Never send account passwords here. */
export async function verifyUserToken(token: string, signal?: AbortSignal): Promise<void> {
  const credential = token.trim()
  if (!credential || credential.length > 256 || /\s/.test(credential)) {
    throw new Error('invalid_token_format')
  }
  const config = { headers: { Authorization: `Bearer ${credential}` }, signal }
  try {
    const { data } = await http.get<unknown>('/auth/me', config)
    if (!data || typeof data !== 'object' || !('authenticated' in data) || data.authenticated !== true
        || !('owner_id' in data) || typeof data.owner_id !== 'string' || !data.owner_id.trim()) {
      throw new Error('invalid_identity_response')
    }
  } catch (cause) {
    // Older backend versions expose user verification through protected history.
    if (!axios.isAxiosError(cause) || cause.response?.status !== 404) throw cause
    const { data } = await http.get<{ items?: unknown; total?: unknown }>('/agent/sessions', {
      ...config, params: { page: 1, page_size: 1 },
    })
    if (!Array.isArray(data?.items) || typeof data.total !== 'number') {
      throw new Error('invalid_identity_response')
    }
  }
}

export function loginError(cause: unknown): string {
  if (cause instanceof Error && cause.message === 'local_session_unavailable') {
    return '本机没有有效授权，请向管理员获取访问令牌，或重新签发本地会话。'
  }
  if (cause instanceof Error && cause.message === 'invalid_token_format') {
    return '请填写有效的访问令牌，长度不超过 256 个字符，且不含空格。'
  }
  if (cause instanceof Error && cause.message === 'invalid_identity_response') {
    return '服务未返回有效的用户身份信息，请联系项目管理员。'
  }
  if (axios.isAxiosError(cause)) {
    if ([401, 403].includes(cause.response?.status || 0)) return '访问令牌无效或已过期，请向管理员获取新的令牌。'
    if (cause.code === 'ECONNABORTED') return '验证超时，请稍后重试。'
  }
  return '暂时无法连接登录服务，请确认后端已启动后重试。'
}

export function loginReturnPath(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')
      || /[\\\u0000-\u0020]/.test(value) || /^\/login(?:[/?#]|$)/i.test(value)) return '/qa'
  return value
}
