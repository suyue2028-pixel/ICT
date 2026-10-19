import type { AnswerMetadata } from '@/api/chat'

// Cache only selection and presentation metadata; backend owns conversation text.
export async function chatStorageKey(token: string, isDemo: boolean) {
  const endpoint = new URL(import.meta.env.VITE_API_BASE_URL || '/api/v1', location.origin).href
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${endpoint}\n${token}`))
  const owner = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
  return `ict:chat:v2:${owner}:${isDemo ? 'demo' : 'real'}`
}

export function savedSelection(key: string) {
  try { return localStorage.getItem(`${key}:active`) } catch { return null }
}
export function saveSelection(key: string, id: string | null) {
  try { localStorage.setItem(`${key}:active`, id || 'new') } catch { /* Backend history remains available. */ }
}
export function savedMetadata(key: string, id: string): AnswerMetadata[] {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(`${key}:meta:${id}`)
      || sessionStorage.getItem(`ict:answer-meta:${id}`) || '[]')
    return Array.isArray(data) ? data.filter(row => row && typeof row === 'object') as AnswerMetadata[] : []
  } catch { return [] }
}
export function saveMetadata(key: string, id: string, metadata: AnswerMetadata[]) {
  try { localStorage.setItem(`${key}:meta:${id}`, JSON.stringify(metadata)) } catch { /* Optional cache. */ }
}
