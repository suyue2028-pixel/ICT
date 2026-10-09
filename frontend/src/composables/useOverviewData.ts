import { computed, shallowRef, toValue } from 'vue'
import type { MaybeRefOrGetter } from 'vue'
import type { EChartsOption } from 'echarts'
import { severityNames, severityOrder } from '@/stores/intelligence'
import type { IntelligenceItem } from '@/types'

export type OverviewMetricKey = 'intelligence' | 'critical' | 'sources' | 'products'
export interface OverviewMetric {
  key: OverviewMetricKey
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
    { key: 'intelligence', label: '情报总数', value: items.value.length, caption: '来源记录数，同一漏洞可有多条', tone: 'blue' },
    {
      key: 'critical',
      label: '严重风险',
      value: items.value.filter((item) => item.severity === 'critical').length,
      caption: '需优先关注',
      tone: 'red',
    },
    {
      key: 'sources',
      label: '来源数量',
      value: new Set(items.value.map((item) => item.source)).size,
      caption: '已记录来源',
      tone: 'teal',
    },
    {
      key: 'products',
      label: '关联产品',
      value: new Set(items.value.flatMap((item) => item.products)).size,
      caption: '来源记录的产品',
      tone: 'violet',
    },
  ])

  const severityOption = computed<EChartsOption>(() => ({
    color: ['#c95465', '#d18b4d', '#c3a54c', '#5b967e', '#a5a5a5'],
    tooltip: {
      trigger: 'item',
      backgroundColor: '#ffffff',
      borderColor: '#e2e2e2',
      textStyle: { color: '#414141', fontSize: 12 },
    },
    series: [
      {
        type: 'pie',
        radius: ['62%', '87%'],
        center: ['50%', '50%'],
        label: { show: false },
        itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
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
      color: ['#c95465', '#d18b4d', '#c3a54c', '#5b967e', '#a5a5a5'],
      legend: {
        top: 0,
        right: 0,
        icon: 'circle',
        itemWidth: 7,
        itemHeight: 7,
        textStyle: { color: '#727272', fontSize: 12 },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#ffffff',
        borderColor: '#e7e7e7',
        textStyle: { color: '#414141', fontSize: 12 },
      },
      grid: { left: 34, right: 18, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates.map((date) => date.slice(5)),
        boundaryGap: false,
        axisLabel: { color: '#727272', fontSize: 12 },
        axisLine: { lineStyle: { color: '#e7e7e7' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
        axisLabel: { color: '#727272', fontSize: 12 },
      },
      series: severityOrder.map((key) => ({
        type: 'line',
        name: severityNames[key],
        smooth: true,
        symbolSize: 6,
        lineStyle: { width: 2 },
        itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
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
        backgroundColor: '#ffffff',
        borderColor: '#e2e2e2',
        textStyle: { color: '#414141', fontSize: 12 },
      },
      grid: { left: 106, right: 28, top: 4, bottom: 28 },
      xAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#ececec', type: 'dashed' } },
        axisLabel: { color: '#747474', fontSize: 12 },
      },
      yAxis: {
        type: 'category',
        data: counts.map((row) => row.name),
        axisLabel: { color: '#656565', fontSize: 12 },
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
          label: { show: true, position: 'right', color: '#656565', fontSize: 12 },
        },
      ],
    }
  })

  return {
    currentItems: items,
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
    uniqueCves: computed(() => new Set(items.value.flatMap(item => item.cve ? [item.cve.toUpperCase()] : [])).size),
  }
}
