<script setup lang="ts">
import type { SidebarSwipeFeedback } from '@/composables/useSidebarSwipeNavigation'

defineProps<{ feedback: SidebarSwipeFeedback | null }>()
</script>

<template>
  <Transition name="sidebar-swipe">
    <div
      v-if="feedback"
      :class="['sidebar-swipe-feedback', { boundary: feedback.boundary }]"
      :style="{ top: `${feedback.y}px` }"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span class="swipe-feedback-copy"
        ><strong>{{ feedback.direction === 'left' ? '向左滑动' : '向右滑动' }} · {{ feedback.label }}</strong
        ><small>{{ feedback.status }}</small></span
      >
      <span class="swipe-progress" aria-hidden="true"
        ><span :style="{ width: `${feedback.progress * 100}%` }"
      /></span>
    </div>
  </Transition>
</template>
