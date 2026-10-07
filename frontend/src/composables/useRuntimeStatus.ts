import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import type { MaybeRefOrGetter } from 'vue'
import { toValue } from 'vue'
import { http } from '@/api/http'

type State = 'unknown' | 'ready' | 'unavailable'
interface Runtime {
  backend: { state: State; label: string }
  agent: { state: State; label: string; mode: string }
  checkedAt: string
}
export function useRuntimeStatus(scope: MaybeRefOrGetter<boolean>) {
  const runtime = shallowRef<Runtime>({ backend: { state: 'unknown', label: '待检查' },
    agent: { state: 'unknown', label: '待检查', mode: 'unknown' }, checkedAt: '' })
  const index = shallowRef<{ indexed: number; pending: number; failed: number } | null>(null)
  const checking = shallowRef(false), indexError = shallowRef(false)
  let request = 0, controller: AbortController | undefined
  let timer: ReturnType<typeof setInterval> | undefined
  async function check() {
    controller?.abort()
    const version = ++request, demo = toValue(scope)
    controller = new AbortController()
    const signal = controller.signal
    checking.value = true
    const health = async () => {
      if (!import.meta.env.DEV) return { backend: { state: 'unknown', label: '未检查' },
        agent: { state: 'unknown', label: '未检查', mode: 'unknown' }, checkedAt: '' } as Runtime
      try {
        const result = await fetch('/__local/runtime', { cache: 'no-store', signal })
        if (!result.ok) throw new Error('bridge_unavailable')
        return await result.json() as Runtime
      } catch {
        return { backend: { state: 'unknown', label: '检查失败' },
          agent: { state: 'unknown', label: '检查失败', mode: 'unknown' }, checkedAt: '' } as Runtime
      }
    }
    const counts = async () => {
      try {
        const rows = await Promise.all(['indexed', 'pending', 'failed', 'queued'].map(status =>
          http.get<{ total: number }>('/knowledge-events', { signal, timeout: 5000,
            params: { is_demo: demo, status, page_size: 1 } })))
        return { indexed: rows[0]!.data.total, pending: rows[1]!.data.total + rows[3]!.data.total,
          failed: rows[2]!.data.total }
      } catch { return null }
    }
    const [status, totals] = await Promise.all([health(), counts()])
    if (version !== request) return
    runtime.value = status; index.value = totals; indexError.value = totals === null
    checking.value = false
  }
  const modeLabel = computed(() => ({ extractive: '本地检索 · 资料摘录', live: '远程模型模式',
    mock: '测试替身模式' })[runtime.value.agent.mode] || '回答模式未确认')
  const checkedTime = computed(() => runtime.value.checkedAt
    ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      .format(new Date(runtime.value.checkedAt)) : '')
  watch(() => toValue(scope), () => { index.value = null; void check() })
  const visible = () => { if (!document.hidden) void check() }
  onMounted(() => { void check(); timer = setInterval(visible, 30_000)
    document.addEventListener('visibilitychange', visible) })
  onBeforeUnmount(() => { request++; controller?.abort(); clearInterval(timer)
    document.removeEventListener('visibilitychange', visible) })
  return { runtime, index, checking, indexError, modeLabel, checkedTime, check }
}
