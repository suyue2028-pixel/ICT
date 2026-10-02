<script setup lang="ts">
import type { OverviewMetric } from '@/composables/useOverviewData'
defineProps<{ metrics: OverviewMetric[]; label?: string }>()
</script>

<template>
  <section class="metric-grid" :aria-label="label || '情报关键指标'">
    <article
      v-for="metric in metrics"
      :key="metric.label"
      :class="['metric-card', `accent-${metric.tone}`]"
    >
      <div class="metric-content">
        <div class="metric-heading">
          <span>{{ metric.label }}</span>
        </div>
        <strong>{{ metric.value }}</strong>
        <small>{{ metric.caption }}</small>
      </div>
    </article>
  </section>
</template>
