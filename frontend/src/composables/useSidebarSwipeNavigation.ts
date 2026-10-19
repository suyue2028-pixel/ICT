import { onBeforeUnmount, onMounted, readonly, shallowRef } from 'vue'
import type { Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isWorkspacePage, workspacePages } from '@/navigation/workspacePages'

const SWIPE_THRESHOLD = 70
const GESTURE_IDLE_MS = 320
const FEEDBACK_LINGER_MS = 600
const INTERACTIVE_TARGET =
  'a, button, input, select, textarea, nav, [role="button"], [contenteditable]'

export interface SidebarSwipeFeedback {
  direction: 'left' | 'right'
  label: string
  status: string
  progress: number
  y: number
  boundary: boolean
}

export function useSidebarSwipeNavigation(sidebar: Readonly<Ref<HTMLElement | null>>) {
  const route = useRoute()
  const router = useRouter()
  const feedback = shallowRef<SidebarSwipeFeedback | null>(null)
  let distance = 0
  let direction = 0
  let consumed = false
  let navigating = false
  let idleTimer: ReturnType<typeof setTimeout> | undefined
  let feedbackTimer: ReturnType<typeof setTimeout> | undefined
  let element: HTMLElement | null = null

  function endGesture() {
    distance = 0
    direction = 0
    consumed = false
    idleTimer = undefined
    feedbackTimer = setTimeout(() => {
      feedback.value = null
      feedbackTimer = undefined
    }, FEEDBACK_LINGER_MS)
  }

  function extendGesture() {
    clearTimeout(idleTimer)
    clearTimeout(feedbackTimer)
    // Include the inertia tail: unlock only after wheel events stop arriving.
    idleTimer = setTimeout(endGesture, GESTURE_IDLE_MS)
  }

  async function navigate(path: string) {
    const activeFeedback = feedback.value
    navigating = true
    try {
      await router.push(path)
      if (feedback.value === activeFeedback && activeFeedback) {
        feedback.value = { ...activeFeedback, status: '已切换到此页' }
      }
    } catch (error) {
      if (feedback.value === activeFeedback && activeFeedback) {
        feedback.value = { ...activeFeedback, status: '切换失败，请点击菜单重试' }
      }
      console.error('滑动切换页面失败', error)
    } finally {
      navigating = false
    }
  }

  function onWheel(event: WheelEvent) {
    if (event.ctrlKey || event.metaKey) return
    const target = event.target
    if (!(target instanceof Element) || target.closest(INTERACTIVE_TARGET)) return

    const { deltaX, deltaY } = event
    if (!Number.isFinite(deltaX) || !Number.isFinite(deltaY)) return
    if (deltaX === 0 || Math.abs(deltaX) < Math.abs(deltaY) * 1.4) {
      if (idleTimer !== undefined) extendGesture()
      distance = 0
      direction = 0
      if (!consumed && !navigating) feedback.value = null
      return
    }

    // A non-passive listener suppresses browser history gestures in this area.
    if (event.cancelable) event.preventDefault()
    extendGesture()
    if (consumed || navigating) return

    const nextDirection = Math.sign(deltaX)
    if (nextDirection !== direction) distance = 0
    direction = nextDirection
    const unit =
      event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element?.clientWidth || 224 : 1
    distance += Math.abs(deltaX) * unit
    const index = workspacePages.findIndex((page) => isWorkspacePage(route.path, page.to))
    if (index < 0) return
    // Natural horizontal scrolling: swiping left gives a positive deltaX.
    const next = workspacePages[index + direction]
    const rect = element?.getBoundingClientRect()
    const y = rect ? Math.max(52, Math.min(rect.height - 52, event.clientY - rect.top)) : 200
    feedback.value = {
      direction: direction > 0 ? 'left' : 'right',
      label: next?.label || (direction > 0 ? '已到最后一页' : '已到第一页'),
      status: next
        ? direction > 0
          ? '继续左滑，前往下一页'
          : '继续右滑，返回上一页'
        : '没有更多页面',
      progress: Math.min(1, distance / SWIPE_THRESHOLD),
      y,
      boundary: !next,
    }
    if (distance < SWIPE_THRESHOLD) return

    // Latch before routing so queued wheel events cannot jump multiple pages.
    consumed = true
    if (next) {
      feedback.value = { ...feedback.value, status: '正在切换…' }
      void navigate(next.to)
    }
  }

  onMounted(() => {
    element = sidebar.value
    element?.addEventListener('wheel', onWheel, { passive: false })
  })
  onBeforeUnmount(() => {
    element?.removeEventListener('wheel', onWheel)
    clearTimeout(idleTimer)
    clearTimeout(feedbackTimer)
  })
  return { feedback: readonly(feedback) }
}
