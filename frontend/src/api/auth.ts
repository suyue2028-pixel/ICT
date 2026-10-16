import axios from 'axios'
import { http, authorizeUser, beginUserAuthorization } from './http'

interface AccountSession { access_token: string; expires_at: string; token_type: string; phone_verified: boolean }
export async function signInAccount(phone: string, password: string, register: boolean, signal?: AbortSignal) {
  const attempt = beginUserAuthorization()
  const { data } = await http.post<AccountSession>(register ? '/auth/register' : '/auth/login', { phone, password }, { signal })
  if (typeof data?.access_token !== 'string' || data.token_type !== 'Bearer' || !(Date.parse(data.expires_at) > Date.now())) {
    throw new Error('invalid_identity_response')
  }
  await authorizeUser(data.access_token, data.expires_at, attempt, signal)
}

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
    const code = cause.response?.data?.error?.code || cause.response?.data?.detail
    const messages: Record<string, string> = {
      invalid_credentials: '手机号或密码错误。',
      weak_password: '密码至少 15 个字符；请避免常见口令、连续重复字符或手机号。',
      registration_unavailable: '暂时无法用此手机号注册，请尝试登录或稍后重试。',
      registration_closed: '当前暂未开放注册，请联系管理员。',
      auth_rate_limited: '尝试次数过多，请稍后再试。', password_work_busy: '登录服务繁忙，请稍后重试。',
      auth_throttle_unavailable: '登录服务暂时不可用，请稍后重试。',
      invalid_request: '请检查手机号和密码格式。',
    }
    if (typeof code === 'string' && messages[code]) return messages[code]
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
