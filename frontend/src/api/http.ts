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
    if (error.response?.status === 504) return '回答超时，请缩小问题范围后重试。'
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
