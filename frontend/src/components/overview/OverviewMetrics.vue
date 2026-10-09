<script setup lang="ts">
import type { OverviewMetric, OverviewMetricKey } from '@/composables/useOverviewData'
defineProps<{ metrics: OverviewMetric[]; label?: string; selectedKey?: OverviewMetricKey | null }>()
defineEmits<{ select: [key: OverviewMetricKey] }>()
</script>

<template>
  <section class="metric-grid" :aria-label="label || '情报关键指标'">
    <button
      v-for="metric in metrics"
      :key="metric.key"
      type="button"
      :class="['metric-card', 'metric-card-button', `accent-${metric.tone}`, { 'is-selected': selectedKey === metric.key }]"
      :aria-label="`${metric.label} ${metric.value}，查看明细`"
      :aria-expanded="selectedKey === metric.key"
      aria-controls="overview-metric-details"
      @click="$emit('select', metric.key)"
    >
      <div class="metric-content">
        <div class="metric-heading">
          <span>{{ metric.label }}</span>
        </div>
        <strong>{{ metric.value }}</strong>
        <small>{{ metric.caption }}</small>
        <span class="metric-action">{{ selectedKey === metric.key ? '收起明细' : '查看明细' }}</span>
      </div>
    </button>
  </section>
</template>

<style scoped>
.metric-card-button { text-align: left; cursor: pointer; color: #000; }
.metric-card-button:hover { outline: 1px solid #000; outline-offset: 2px; }
.metric-card-button.is-selected { outline: 2px solid #000; outline-offset: 2px; }
.metric-card-button:focus-visible { outline: 2px solid #000; outline-offset: 4px; }
.metric-action { display: inline-block; margin-top: 12px; color: #000; font-size: 11px; text-decoration: underline; }
</style>
