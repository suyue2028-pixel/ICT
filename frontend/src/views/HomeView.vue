<script setup lang="ts">
import PageState from '@/components/PageState.vue'
import OverviewMetrics from '@/components/overview/OverviewMetrics.vue'
import OverviewInsights from '@/components/overview/OverviewInsights.vue'
import { useOverviewData } from '@/composables/useOverviewData'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const {
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
const steps = ['情报采集', '规范与富化', '知识关联', '可追溯问答']
</script>

<template>
  <div class="page-heading overview-heading">
    <div>
      <span class="eyebrow">工作空间 / 总览</span>
      <h1>情报总览</h1>
      <p>从情报采集到可追溯问答，一屏查看真实运行记录。</p>
    </div>
    <div class="overview-heading-actions">
      <router-link class="primary-link overview-all-link" to="/intelligence"
        >查看全部情报 <span aria-hidden="true">→</span></router-link
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
      <OverviewMetrics :metrics="metrics" />
      <p class="overview-summary">当前时间范围：{{ total }} 条来源记录 · {{ uniqueCves }} 个已记录的去重 CVE。未提供 CVE 的资料不计入该数量。</p>
      <section class="flow-strip" aria-label="系统处理流程">
        <h2>处理流程</h2>
        <div v-for="(step, index) in steps" :key="step" class="flow-step">
          <span class="flow-number">0{{ index + 1 }}</span
          ><strong>{{ step }}</strong>
        </div>
        <small>后端采集 · Agent 检索</small>
      </section>
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
.overview-summary { grid-column: 1 / -1; color: #667085; font-size: 11px; line-height: 1.7; margin: -10px 0 0; }
</style>
