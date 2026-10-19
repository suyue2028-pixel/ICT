<script setup lang="ts">
import { onBeforeUnmount, onMounted, useTemplateRef, watch } from 'vue'
import { init, use } from 'echarts/core'
import type { EChartsType } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer, SVGRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
  SVGRenderer,
])

const props = defineProps<{
  option: EChartsOption
  height?: string
  label?: string
  renderer?: 'canvas' | 'svg'
}>()
const emit = defineEmits<{ nodeClick: [id: string] }>()
const element = useTemplateRef<HTMLElement>('chartEl')
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
function resetView() {
  chart?.clear()
  chart?.setOption({ ...props.option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches })
}
defineExpose({ resetView })

onMounted(() => {
  if (!element.value) return
  chart = init(element.value, undefined, { renderer: props.renderer || 'canvas' })
  chart.setOption({
    ...props.option,
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  })
  chart.on('click', (event) => {
    if (
      event.dataType === 'node' &&
      event.data &&
      typeof event.data === 'object' &&
      'id' in event.data
    ) {
      emit('nodeClick', String(event.data.id))
    }
  })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value)
})
watch(
  () => props.option,
  (option) =>
    chart?.setOption(
      { ...option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches },
      true,
    ),
)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div
    ref="chartEl"
    class="chart"
    :style="{ height: height || '280px' }"
    role="img"
    :aria-label="label || '资料统计图表'"
  />
</template>
