import { computed, shallowRef, toValue } from 'vue'
import type { MaybeRefOrGetter } from 'vue'
import type { EChartsOption } from 'echarts'
import { severityNames, severityOrder } from '@/stores/intelligence'
import type { IntelligenceItem } from '@/types'

export interface OverviewMetric {
  label: string
  value: number
  caption: string
  tone: 'blue' | 'red' | 'teal' | 'violet'
}

export function useOverviewData(source: MaybeRefOrGetter<IntelligenceItem[]>) {
  const allItems = computed(() => toValue(source))
  const period = shallowRef(30)
  const periods = [
    { days: 7, label: '7天' },
    { days: 30, label: '30天' },
    { days: 90, label: '90天' },
    { days: 365, label: '1年' },
  ]
  const latestDate = computed(
    () =>
      [...allItems.value]
        .map((item) => item.publishedAt)
        .sort()
        .at(-1) || '',
  )
  const items = computed(() => {
    if (!latestDate.value) return []
    const end = Date.parse(`${latestDate.value}T00:00:00Z`)
    const start = end - (period.value - 1) * 86400000
    return allItems.value.filter((item) => {
      const date = Date.parse(`${item.publishedAt}T00:00:00Z`)
      return date >= start && date <= end
    })
  })
  const recent = computed(() =>
    [...items.value].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)).slice(0, 5),
  )
  const priority = computed(() =>
    [...items.value]
      .filter((item) => item.severity === 'critical' || item.severity === 'high')
      .sort((a, b) => b.publishedAt.localeCompare(a.publishedAt))
      .slice(0, 5),
  )
  const riskSummary = computed(() =>
    severityOrder.map((key) => {
      const count = items.value.filter((item) => item.severity === key).length
      return {
        key,
        name: severityNames[key],
        count,
        percentage: items.value.length
          ? `${((count / items.value.length) * 100).toFixed(1)}%`
          : '0%',
      }
    }),
  )
  const metrics = computed<OverviewMetric[]>(() => [
    { label: '情报总数', value: items.value.length, caption: '本地演示记录', tone: 'blue' },
    {
      label: '严重风险',
      value: items.value.filter((item) => item.severity === 'critical').length,
      caption: '需优先关注',
      tone: 'red',
    },
    {
      label: '来源类别',
      value: new Set(items.value.map((item) => item.source)).size,
      caption: '均为模拟来源',
      tone: 'teal',
    },
    {
      label: '关联产品',
      value: new Set(items.value.flatMap((item) => item.products)).size,
      caption: '示例资产',
      tone: 'violet',
    },
  ])

  const severityOption = computed<EChartsOption>(() => ({
    color: ['#ed6171', '#f39b57', '#efc354', '#4bbb98'],
    tooltip: {
      trigger: 'item',
      backgroundColor: '#fff',
      borderColor: '#e4deed',
      textStyle: { color: '#414047', fontSize: 12 },
    },
    series: [
      {
        type: 'pie',
        radius: ['62%', '87%'],
        center: ['50%', '50%'],
        label: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        data: severityOrder.map((key) => ({
          name: severityNames[key],
          value: items.value.filter((item) => item.severity === key).length,
        })),
      },
    ],
  }))

  const trendOption = computed<EChartsOption>(() => {
    const dates = [...new Set(items.value.map((item) => item.publishedAt))].sort()
    return {
      color: ['#ed6171', '#f39b57', '#efc354', '#4bbb98'],
      legend: {
        top: 0,
        right: 0,
        icon: 'circle',
        itemWidth: 7,
        itemHeight: 7,
        textStyle: { color: '#64748b', fontSize: 12 },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#fff',
        borderColor: '#e5e7eb',
        textStyle: { color: '#414047', fontSize: 12 },
      },
      grid: { left: 34, right: 18, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates.map((date) => date.slice(5)),
        boundaryGap: false,
        axisLabel: { color: '#6b7280', fontSize: 12 },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#eef0f3', type: 'dashed' } },
        axisLabel: { color: '#6b7280', fontSize: 12 },
      },
      series: severityOrder.map((key) => ({
        type: 'line',
        name: severityNames[key],
        smooth: true,
        symbolSize: 6,
        lineStyle: { width: 2 },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        areaStyle: { opacity: 0.06 },
        data: dates.map(
          (date) =>
            items.value.filter((item) => item.publishedAt === date && item.severity === key).length,
        ),
      })),
    }
  })

  const sourceOption = computed<EChartsOption>(() => {
    const counts = [...new Set(items.value.map((item) => item.source))]
      .map((sourceName) => ({
        name: sourceName,
        count: items.value.filter((item) => item.source === sourceName).length,
      }))
      .sort((a, b) => a.count - b.count)
    return {
      color: ['#6b9d82'],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: '#fff',
        borderColor: '#e4deed',
        textStyle: { color: '#414047', fontSize: 12 },
      },
      grid: { left: 106, right: 28, top: 4, bottom: 28 },
      xAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#edeaf1', type: 'dashed' } },
        axisLabel: { color: '#75727d', fontSize: 12 },
      },
      yAxis: {
        type: 'category',
        data: counts.map((row) => row.name),
        axisLabel: { color: '#65636e', fontSize: 12 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          name: '情报数量',
          data: counts.map((row) => row.count),
          barMaxWidth: 9,
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: 'right', color: '#65636e', fontSize: 12 },
        },
      ],
    }
  })

  return {
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
    total: computed(() => items.value.length),
  }
}
