import axios from 'axios'

// API paths and response shapes are pending team confirmation. No demo page calls this client.
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  timeout: 15_000,
})

export async function apiGet<T>(path: string, params?: Record<string, string | number>) {
  const response = await http.get<T>(path, { params })
  return response.data
}

export async function apiPost<TResponse, TBody>(path: string, body: TBody) {
  const response = await http.post<TResponse>(path, body)
  return response.data
}
