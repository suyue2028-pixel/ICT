<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import IntelligenceTable from '@/components/intelligence/IntelligenceTable.vue'
import PageState from '@/components/PageState.vue'
import type { OverviewMetricKey } from '@/composables/useOverviewData'
import type { IntelligenceItem } from '@/types'

const props = defineProps<{ kind: OverviewMetricKey; items: IntelligenceItem[]; period: number; latestDate: string; isDemo: boolean }>()
defineEmits<{ close: [] }>()
const group = shallowRef(''), page = shallowRef(1)
const pageSize = 5
const headings: Record<OverviewMetricKey, string> = {
  intelligence: '情报总数明细', critical: '严重风险情报', sources: '来源及其情报', products: '产品及其关联情报',
}
const emptyMessages: Record<OverviewMetricKey, string> = {
  intelligence: '当前时间范围内暂无情报。', critical: '当前时间范围内暂无严重风险情报。',
  sources: '当前时间范围内暂无来源情报。', products: '当前时间范围内暂无已记录的产品关联。',
}
const grouped = computed(() => props.kind === 'sources' || props.kind === 'products')
const groups = computed(() => {
  const values = props.kind === 'sources'
    ? props.items.map(item => item.source)
    : props.kind === 'products' ? props.items.flatMap(item => item.products) : []
  return [...new Set(values)].sort((a, b) => a.localeCompare(b, 'zh-CN')).map(name => ({
    name, count: props.items.filter(item => props.kind === 'sources' ? item.source === name : item.products.includes(name)).length,
  }))
})
const records = computed(() => props.items.filter(item => {
  if (props.kind === 'critical') return item.severity === 'critical'
  if (props.kind === 'sources') return !group.value || item.source === group.value
  if (props.kind === 'products') return item.products.length > 0 && (!group.value || item.products.includes(group.value))
  return true
}).sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)))
const pageCount = computed(() => Math.max(1, Math.ceil(records.value.length / pageSize)))
const rows = computed(() => records.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(() => [props.kind, props.period, props.latestDate, props.isDemo], () => { group.value = ''; page.value = 1 })
watch(() => props.items, () => {
  if (!groups.value.some(item => item.name === group.value)) group.value = ''
  page.value = Math.min(page.value, pageCount.value)
})
function selectGroup(name: string) { group.value = name; page.value = 1 }
function changePage(value: number) { page.value = Math.max(1, Math.min(value, pageCount.value)) }
</script>

<template>
  <section id="overview-metric-details" class="panel metric-details" aria-labelledby="metric-details-title">
    <div class="panel-title">
      <div>
        <h2 id="metric-details-title">{{ headings[kind] }}</h2>
        <p>{{ isDemo ? '虚构样本' : '真实资料' }} · 截至 {{ latestDate || '暂无日期' }} 的最近 {{ period }} 天，与首页统计范围一致</p>
      </div>
      <button class="details-close" type="button" @click="$emit('close')">收起明细</button>
    </div>
    <template v-if="grouped && groups.length">
      <div class="metric-groups" :aria-label="kind === 'sources' ? '按来源查看情报' : '按产品查看情报'">
        <button type="button" :aria-pressed="!group" :class="{ active: !group }" @click="selectGroup('')">{{ kind === 'sources' ? '全部来源' : '全部产品' }}</button>
        <button v-for="item in groups" :key="item.name" type="button" :aria-pressed="group === item.name" :class="{ active: group === item.name }" @click="selectGroup(item.name)">{{ item.name }} · {{ item.count }} 条情报</button>
      </div>
      <p class="metric-detail-note">{{ groups.length }} {{ kind === 'sources' ? '个来源' : '个产品' }}。{{ kind === 'products' ? '同一条情报可能关联多个产品。' : '选择来源查看其具体情报。' }}</p>
    </template>
    <p class="metric-result-count">{{ group || '当前范围' }}：{{ records.length }} 条{{ kind === 'critical' ? '严重风险情报' : '相关情报' }}</p>
    <PageState :empty="records.length === 0" :empty-text="emptyMessages[kind]">
      <IntelligenceTable :rows="rows" :page="page" :page-count="pageCount" :total="records.length" @change-page="changePage" />
    </PageState>
  </section>
</template>

<style scoped>
.metric-details { background: #fff; color: #000; border: 1px solid #000; }
.details-close { border: 1px solid #000; background: #fff; color: #000; border-radius: 18px; padding: 7px 12px; font-size: 12px; flex-shrink: 0; }
.metric-groups { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 10px; }
.metric-groups button { border: 1px solid #000; background: #fff; color: #000; border-radius: 16px; padding: 7px 12px; font-size: 12px; overflow-wrap: anywhere; max-width: 100%; }
.metric-groups button.active { background: #000; color: #fff; }
.metric-detail-note, .metric-result-count { margin: 10px 0; font-size: 12px; color: #000; line-height: 1.7; }
.details-close:focus-visible, .metric-groups button:focus-visible { outline: 2px solid #000; outline-offset: 3px; }
.metric-details :deep(.page-state) { min-height: 140px; color: #000; }
</style>
