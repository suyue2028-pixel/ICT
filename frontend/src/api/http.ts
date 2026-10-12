import axios from 'axios'
import { shallowRef } from 'vue'
import { classifyFailure, isVerifiedUserIdentity, knownSessionExpired, shouldInvalidateCredential } from '@/utils/requestFailure'

// User credentials stay in memory. Platform/admin/model keys never belong here.
export const userToken = shallowRef('')
export const sessionState = shallowRef<'unauthorized' | 'checking' | 'authorized' | 'expired'>('unauthorized')
export const sessionExpiresAt = shallowRef<string | null>(null)
let sessionAttempt: Promise<void> | undefined
export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1', timeout: 65_000 })
export class UserAuthorizationRequired extends Error {
  constructor() { super('user_token_required') }
}
export function clearUserSession(expired = true) {
  userToken.value = ''
  sessionExpiresAt.value = null
  sessionState.value = expired ? 'expired' : 'unauthorized'
}
http.interceptors.request.use(config => {
  // Explicit credentials are used while validating a new user token.
  if (userToken.value && !config.headers.Authorization) config.headers.Authorization = `Bearer ${userToken.value}`
  return config
})
http.interceptors.response.use(response => response, error => {
  // A failed old request must not invalidate a newly authorized session.
  if (axios.isAxiosError(error) && shouldInvalidateCredential(error.config?.headers?.Authorization, userToken.value,
    { status: error.response?.status, data: error.response?.data })) clearUserSession(true)
  return Promise.reject(error)
})
export async function authorizeUser(token: string, expiresAt?: string | null) {
  const clean = token.trim()
  if (!clean || clean.length > 256 || /\s/.test(clean)) throw new UserAuthorizationRequired()
  const previousState = sessionState.value
  sessionState.value = 'checking'
  try {
    const config = { headers: { Authorization: `Bearer ${clean}` } }
    try {
      const response = await http.get('/auth/me', config)
      if (!isVerifiedUserIdentity(response.data)) throw new Error('invalid_user_identity_response')
    } catch (error) {
      // Older backend releases can validate with the existing require_user endpoint.
      if (!axios.isAxiosError(error) || error.response?.status !== 404) throw error
      const response = await http.get('/agent/sessions', { ...config, params: { page: 1, page_size: 1 } })
      if (!Array.isArray(response.data?.items) || typeof response.data?.total !== 'number') throw new Error('invalid_user_identity_response')
    }
    userToken.value = clean
    sessionExpiresAt.value = expiresAt || null
    sessionState.value = 'authorized'
  } catch (error) {
    sessionState.value = userToken.value ? 'authorized' : previousState === 'expired' ? 'expired' : 'unauthorized'
    throw error
  }
}
export async function ensureSession(force = false) {
  if (!force && userToken.value && !knownSessionExpired(sessionExpiresAt.value)) return
  if (userToken.value && knownSessionExpired(sessionExpiresAt.value)) clearUserSession(true)
  if (!import.meta.env.DEV) return
  if (!sessionAttempt) {
    sessionAttempt = (async () => {
      try {
        // Read an administrator-issued credential. This endpoint never issues one.
        const response = await fetch('/__local/session', { cache: 'no-store' })
        if (!response.ok) return
        const result = await response.json() as { access_token?: string; expires_at?: string }
        if (typeof result.access_token === 'string' && Date.parse(result.expires_at || '') > Date.now()) await authorizeUser(result.access_token, result.expires_at)
      } catch { /* Keep explicit user reauthorization available. */ }
    })().finally(() => { sessionAttempt = undefined })
  }
  await sessionAttempt
}
export function requestFailure(error: unknown) {
  if (error instanceof UserAuthorizationRequired) return classifyFailure({ status: 401, data: { detail: 'user_token_required' } })
  if (axios.isAxiosError(error)) return classifyFailure({ status: error.response?.status, data: error.response?.data, code: error.code })
  return { kind: 'service' as const, heading: '请求失败', message: '请求失败，问题已保留，请重试。' }
}
export function errorMessage(error: unknown) { return requestFailure(error).message }
export async function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
  return (await http.get<T>(path, { params })).data
}
export async function apiPost<TResponse, TBody>(path: string, body: TBody) { return (await http.post<TResponse>(path, body)).data }
