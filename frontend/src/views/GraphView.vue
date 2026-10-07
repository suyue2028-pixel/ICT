<script setup lang="ts">
import { computed, nextTick, onMounted, shallowRef, useTemplateRef, watch } from 'vue'
import { errorMessage } from '@/api/http'
import type { EChartsOption } from 'echarts'
import ChartCanvas from '@/components/ChartCanvas.vue'
import PageState from '@/components/PageState.vue'
import GraphToolbar from '@/components/GraphToolbar.vue'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const selectedId = shallowRef('')
const search = shallowRef(''), category = shallowRef('all')
const focus = shallowRef(true)
const graphCanvas = useTemplateRef<InstanceType<typeof ChartCanvas>>('graphCanvas')
const availableCategories = computed(() => [...new Set(store.graphNodes.map(node => node.category))])
const categoryNodes = computed(() => store.graphNodes.filter(node => category.value === 'all' || node.category === category.value))
const filteredNodes = computed(() => store.graphNodes.filter(node =>
  (category.value === 'all' || node.category === category.value)
  && node.name.toLowerCase().includes(search.value.trim().toLowerCase())))
const visibleIds = computed(() => new Set(filteredNodes.value.map(node => node.id)))
const neighborhood = computed(() => new Set([selectedId.value, ...store.graphLinks
  .filter(link => link.source === selectedId.value || link.target === selectedId.value)
  .flatMap(link => [link.source, link.target])]))
const displayedNodes = computed(() => {
  if (search.value.trim()) {
    const matches = visibleIds.value
    const ids = new Set([...matches, ...store.graphLinks.filter(link => matches.has(link.source) || matches.has(link.target)).flatMap(link => [link.source, link.target])])
    return categoryNodes.value.filter(node => ids.has(node.id))
  }
  return focus.value ? categoryNodes.value.filter(node => neighborhood.value.has(node.id)) : categoryNodes.value
})
const displayedIds = computed(() => new Set(displayedNodes.value.map(node => node.id)))
watch(filteredNodes, nodes => { if (search.value.trim() && nodes.length === 1) selectedId.value = nodes[0]!.id })
async function resetView() { search.value = ''; category.value = 'all'; focus.value = true; await nextTick(); graphCanvas.value?.resetView() }
watch(categoryNodes, nodes => { if (!nodes.some(node => node.id === selectedId.value)) selectedId.value = nodes[0]?.id || '' })
watch(() => store.isDemo, () => { search.value = ''; category.value = 'all' })
const graphLoading = shallowRef(false), graphError = shallowRef('')
async function loadGraph() { graphLoading.value = true; graphError.value = ''; try { await store.loadGraph(); selectedId.value = store.graphNodes[0]?.id || '' } catch (error) { graphError.value = errorMessage(error) } finally { graphLoading.value = false } }
onMounted(() => { void loadGraph() })
watch(() => store.isDemo, () => { void loadGraph() })
const selected = computed(() => store.graphNodes.find((node) => node.id === selectedId.value))
const related = computed(
  () =>
    selected.value?.intelligenceIds
      .map((id) => store.findById(id))
      .filter((item) => item !== undefined) || [],
)
const categories = ['情报', '漏洞', '产品', '弱点', '来源']
const colors = ['#8670b5', '#ed6171', '#549e99', '#b39a5c', '#7e9e62']
const graphOption = computed<EChartsOption>(() => ({
  tooltip: { show: false },
  legend: [
    {
      data: availableCategories.value,
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
      force: { repulsion: displayedNodes.value.length > 15 ? 120 : 500,
        edgeLength: displayedNodes.value.length > 15 ? 60 : 150, gravity: 0.3, initLayout: 'circular' },
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
      data: displayedNodes.value.map((node) => ({
        id: node.id,
        name: node.name,
        category: categories.indexOf(node.category),
        symbolSize: node.category === '情报' ? 64 : 48,
        itemStyle: {
          color: colors[categories.indexOf(node.category)],
          borderColor: selectedId.value === node.id ? '#4262ed' : '#fff',
          borderWidth: selectedId.value === node.id ? 4 : 2,
          opacity: search.value.trim() ? (visibleIds.value.has(node.id) ? 1 : 0.75) : (neighborhood.value.has(node.id) ? 1 : 0.75),
        },
        label: { opacity: 1, width: 180, overflow: 'truncate' },
      })),
      links: store.graphLinks.filter(link => displayedIds.value.has(link.source) && displayedIds.value.has(link.target)).map((link) => ({
        source: link.source,
        target: link.target,
        relation: link.relation,
        lineStyle: { opacity: neighborhood.value.has(link.source) && neighborhood.value.has(link.target) ? 1 : 0.3,
          color: link.source === selectedId.value || link.target === selectedId.value ? '#536db2' : '#8290a8' },
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
    <span class="heading-count">{{ store.graphNodes.length }} 个来源实体</span>
  </div>
  <PageState
    :loading="graphLoading"
    :error="graphError"
    :empty="store.graphNodes.length === 0"
    @retry="loadGraph"
  >
    <div class="graph-layout">
      <section class="panel graph-panel">
        <div class="panel-title">
          <div>
            <h2>实体关系网络</h2>
            <p>漏洞与来源文档关联；载入最多30个漏洞。{{ focus ? '当前聚焦实体及直接关联' : '当前显示完整已载入图谱' }}</p>
          </div>
          <span class="panel-index">拖拽 · 缩放 · 点击</span>
        </div>
        <GraphToolbar v-model:search="search" v-model:category="category" v-model:focus="focus" :categories="availableCategories" :count="filteredNodes.length" :total="store.graphNodes.length" @reset="resetView" />
        <ChartCanvas
          ref="graphCanvas"
          :option="graphOption"
          :height="focus ? 'clamp(280px, 38vh, 400px)' : 'clamp(400px, 60vh, 540px)'"
          renderer="svg"
          label="来源实体关系图；下方提供可用键盘选择的实体列表"
          @node-click="selectedId = $event"
        />
        <div class="graph-entity-list" aria-label="选择图谱实体">
          <button
            v-for="node in filteredNodes"
            :key="node.id"
            type="button"
            :class="['entity-button', { selected: selectedId === node.id }]"
            :aria-pressed="selectedId === node.id"
            @click="selectedId = node.id"
          >
            {{ node.name }}
          </button>
          <p v-if="!filteredNodes.length" class="muted" role="status">没有匹配的实体，请更换关键词或恢复视图。</p>
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
