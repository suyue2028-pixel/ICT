<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import type { EChartsOption } from 'echarts'
import ChartCanvas from '@/components/ChartCanvas.vue'
import PageState from '@/components/PageState.vue'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const selectedId = shallowRef('i-1')
const selected = computed(() => store.graphNodes.find((node) => node.id === selectedId.value))
const related = computed(
  () =>
    selected.value?.intelligenceIds
      .map((id) => store.findById(id))
      .filter((item) => item !== undefined) || [],
)
const categories = ['情报', '产品', '弱点', '来源']
const colors = ['#8670b5', '#549e99', '#b39a5c', '#7e9e62']
const graphOption = computed<EChartsOption>(() => ({
  tooltip: { show: false },
  legend: [
    {
      data: categories,
      top: 0,
      icon: 'circle',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 20,
      textStyle: { color: '#44516a', fontSize: 13 },
    },
  ],
  animationDuration: 600,
  series: [
    {
      type: 'graph',
      left: 38,
      right: 38,
      top: 48,
      bottom: 64,
      layout: 'force',
      roam: true,
      zoom: 0.82,
      draggable: true,
      force: { repulsion: 900, edgeLength: 170, gravity: 0.14, initLayout: 'circular' },
      label: {
        show: true,
        position: 'bottom',
        distance: 8,
        color: '#27354a',
        fontSize: 14,
        fontWeight: 500,
        backgroundColor: '#ffffffed',
        padding: [4, 6],
        borderRadius: 4,
      },
      edgeLabel: {
        show: true,
        formatter: (params: unknown) => {
          const p = params as { data?: { relation?: string } }
          return p.data?.relation || ''
        },
        color: '#4e5e77',
        fontSize: 12,
        fontWeight: 500,
        backgroundColor: '#ffffffed',
        padding: [3, 5],
        borderRadius: 3,
      },
      lineStyle: { color: '#8290a8', width: 2, opacity: 0.9, curveness: 0.08 },
      emphasis: {
        focus: 'none',
        label: { color: '#172033', fontWeight: 600 },
        lineStyle: { color: '#5f6f8b', width: 3, opacity: 1 },
      },
      categories: categories.map((name, index) => ({ name, itemStyle: { color: colors[index] } })),
      data: store.graphNodes.map((node) => ({
        id: node.id,
        name: node.name,
        category: categories.indexOf(node.category),
        symbolSize: node.category === '情报' ? 64 : 48,
        itemStyle: {
          color: colors[categories.indexOf(node.category)],
          borderColor: selectedId.value === node.id ? '#4262ed' : '#fff',
          borderWidth: selectedId.value === node.id ? 4 : 2,
        },
      })),
      links: store.graphLinks.map((link) => ({
        source: link.source,
        target: link.target,
        relation: link.relation,
      })),
    },
  ],
}))
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">工作空间 / 关联探索</span>
      <h1>知识关联图谱</h1>
      <p>点击图谱节点或下方实体名称，查看说明及关联情报。</p>
    </div>
    <span class="heading-count">{{ store.graphNodes.length }} 个演示实体</span>
  </div>
  <PageState
    :loading="store.loading || !store.initialized"
    :error="store.error"
    :empty="store.graphNodes.length === 0"
    @retry="store.loadDemo"
  >
    <div class="graph-layout">
      <section class="panel graph-panel">
        <div class="panel-title">
          <div>
            <h2>实体关系网络</h2>
            <p>关系根据本地演示记录构建</p>
          </div>
          <span class="panel-index">拖拽 · 缩放 · 点击</span>
        </div>
        <ChartCanvas
          :option="graphOption"
          height="540px"
          renderer="svg"
          label="演示实体关系图；下方提供可用键盘选择的实体列表"
          @node-click="selectedId = $event"
        />
        <div class="graph-entity-list" aria-label="选择图谱实体">
          <button
            v-for="node in store.graphNodes"
            :key="node.id"
            type="button"
            :class="['entity-button', { selected: selectedId === node.id }]"
            :aria-pressed="selectedId === node.id"
            @click="selectedId = node.id"
          >
            {{ node.name }}
          </button>
        </div>
      </section>
      <aside class="panel graph-detail">
        <span class="eyebrow">当前实体</span>
        <div v-if="selected">
          <span class="soft-tag">{{ selected.category }}</span>
          <h2>{{ selected.name }}</h2>
          <p>{{ selected.description }}</p>
          <div class="aside-divider" />
          <h3>相关情报</h3>
          <div v-if="related.length" class="related-list">
            <router-link v-for="item in related" :key="item.id" :to="`/intelligence/${item.id}`"
              ><strong>{{ item.title }}</strong
              ><small>{{ item.id }} ↗</small></router-link
            >
          </div>
          <p v-else class="muted">暂无数据</p>
        </div>
        <p v-else class="muted">点击图谱节点查看详情。</p>
      </aside>
    </div>
  </PageState>
</template>
