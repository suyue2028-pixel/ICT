<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import ChartCanvas from '@/components/ChartCanvas.vue'
import OverviewPriorityTable from './OverviewPriorityTable.vue'
import OverviewRecentFeed from './OverviewRecentFeed.vue'
import type { IntelligenceItem, Severity } from '@/types'

defineProps<{
  severityOption: EChartsOption
  trendOption: EChartsOption
  sourceOption: EChartsOption
  recent: IntelligenceItem[]
  priority: IntelligenceItem[]
  riskSummary: { key: Severity; name: string; count: number; percentage: string }[]
  total: number
}>()
</script>

<template>
  <div class="dashboard-grid overview-charts">
    <section class="panel trend-panel">
      <div class="panel-title">
        <h2>发布时间趋势</h2>
      </div>
      <ChartCanvas
        :option="trendOption"
        height="238px"
        label="按发布日期和风险等级统计的来源资料趋势图"
      />
    </section>
    <section class="panel risk-panel">
      <div class="panel-title">
        <h2>风险等级分布</h2>
      </div>
      <div class="risk-content">
        <div class="risk-donut">
          <ChartCanvas :option="severityOption" height="238px" label="来源资料风险等级分布图" />
          <div class="donut-caption">
            <strong>{{ total }}</strong
            ><small>情报总数</small>
          </div>
        </div>
        <ul class="risk-legend" aria-label="风险等级数量和占比">
          <li v-for="risk in riskSummary" :key="risk.key">
            <span :class="['risk-dot', risk.key]" aria-hidden="true" /><span>{{ risk.name }}</span
            ><strong>{{ risk.count }}</strong
            ><small>{{ risk.percentage }}</small>
          </li>
        </ul>
      </div>
    </section>
  </div>
  <div class="dashboard-grid overview-records">
    <OverviewPriorityTable :items="priority" />
    <OverviewRecentFeed :items="recent" />
  </div>
  <section class="panel sources-panel">
    <div class="panel-title">
      <div>
        <h2>来源统计</h2>
        <p>按当前时间范围内的情报统计，统计来自当前已加载的来源资料</p>
      </div>
      <span class="panel-index">已记录来源</span>
    </div>
    <ChartCanvas :option="sourceOption" height="205px" label="来源资料来源统计图" />
  </section>
</template>
