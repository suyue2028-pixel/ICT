<script setup lang="ts">
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
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
      <span :class="['swipe-direction-icon', feedback.direction]" aria-hidden="true">
        <ArrowLeft v-if="feedback.direction === 'left'" />
        <ArrowRight v-else />
      </span>
      <span class="swipe-feedback-copy"
        ><strong>{{ feedback.label }}</strong
        ><small>{{ feedback.status }}</small></span
      >
      <span class="swipe-progress" aria-hidden="true"
        ><span :style="{ width: `${feedback.progress * 100}%` }"
      /></span>
    </div>
  </Transition>
</template>
