<script setup lang="ts">
import { shallowRef } from 'vue'
import PageState from '@/components/PageState.vue'
import OverviewMetrics from '@/components/overview/OverviewMetrics.vue'
import OverviewInsights from '@/components/overview/OverviewInsights.vue'
import OverviewMetricDetails from '@/components/overview/OverviewMetricDetails.vue'
import { useOverviewData } from '@/composables/useOverviewData'
import type { OverviewMetricKey } from '@/composables/useOverviewData'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const {
  currentItems,
  metrics,
  severityOption,
  trendOption,
  sourceOption,
  period,
  periods,
  latestDate,
  recent,
  priority,
  riskSummary,
  total,
  uniqueCves,
} = useOverviewData(() => store.items)
const activeMetric = shallowRef<OverviewMetricKey | null>(null)
function selectMetric(key: OverviewMetricKey) { activeMetric.value = activeMetric.value === key ? null : key }
</script>

<template>
  <div class="page-heading overview-heading">
    <div>
      <h1>首页</h1>
      <p>查看安全情报、风险分布与原始来源。</p>
    </div>
    <div class="overview-heading-actions">
      <router-link class="primary-link overview-all-link" to="/intelligence"
        >查看全部情报</router-link
      >
      <div class="overview-period">
        <div class="period-switch" role="group" aria-label="统计时间范围">
          <button
            v-for="option in periods"
            :key="option.days"
            type="button"
            :aria-pressed="period === option.days"
            :class="{ active: period === option.days }"
            @click="period = option.days"
          >
            {{ option.label }}
          </button>
        </div>
        <small>截至资料日期 {{ latestDate || '暂无数据' }}</small>
      </div>
    </div>
  </div>
  <PageState
    :loading="store.loading || !store.initialized"
    :error="store.error"
    :empty="store.items.length === 0"
    @retry="store.loadDemo"
  >
    <div class="overview-layout">
      <OverviewMetrics :metrics="metrics" :selected-key="activeMetric" @select="selectMetric" />
      <p class="overview-summary">当前时间范围：{{ total }} 条来源记录 · {{ uniqueCves }} 个已记录的去重 CVE。未提供 CVE 的资料不计入该数量。</p>
      <OverviewMetricDetails v-if="activeMetric" :kind="activeMetric" :items="currentItems" :period="period" :latest-date="latestDate" :is-demo="store.isDemo" @close="activeMetric = null" />
      <OverviewInsights
        :severity-option="severityOption"
        :trend-option="trendOption"
        :source-option="sourceOption"
        :recent="recent"
        :priority="priority"
        :risk-summary="riskSummary"
        :total="total"
      />
    </div>
  </PageState>
</template>

<style scoped>
.overview-summary { grid-column: 1 / -1; color: #6f6f6f; font-size: 11px; line-height: 1.7; margin: -10px 0 0; }
</style>
