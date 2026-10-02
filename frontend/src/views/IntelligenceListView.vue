<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import IntelligenceFilters from '@/components/intelligence/IntelligenceFilters.vue'
import IntelligenceTable from '@/components/intelligence/IntelligenceTable.vue'
import PageState from '@/components/PageState.vue'
import { severityNames, useIntelligenceStore } from '@/stores/intelligence'
import type { Severity } from '@/types'

const store = useIntelligenceStore()
const route = useRoute()
const router = useRouter()
const pageSize = 5

function queryText(value: unknown) {
  return typeof value === 'string' ? value : ''
}
function updateQuery(patch: Record<string, string | undefined>) {
  void router.replace({ query: { ...route.query, ...patch } })
}
const search = computed({
  get: () => queryText(route.query.q),
  set: (value: string) => updateQuery({ q: value.trim() || undefined, page: undefined }),
})
const severity = computed<Severity | 'all'>({
  get: () => {
    const value = queryText(route.query.severity)
    return value in severityNames ? (value as Severity) : 'all'
  },
  set: (value) => updateQuery({ severity: value === 'all' ? undefined : value, page: undefined }),
})
const filtered = computed(() =>
  store.latest.filter((item) => {
    const needle = search.value.trim().toLocaleLowerCase('zh-CN')
    const text =
      `${item.id} ${item.cve || ''} ${item.cwe || ''} ${item.source} ${item.title} ${item.summary} ${item.tags.join(' ')} ${item.products.join(' ')}`.toLocaleLowerCase(
        'zh-CN',
      )
    return text.includes(needle) && (severity.value === 'all' || item.severity === severity.value)
  }),
)
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const page = computed(() => {
  const value = Number(queryText(route.query.page))
  return Number.isInteger(value) && value > 0 ? Math.min(value, pageCount.value) : 1
})
const rows = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize),
)
function changePage(next: number) {
  updateQuery({ page: next === 1 ? undefined : String(next) })
}
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">工作空间 / 情报记录</span>
      <h1>情报中心</h1>
      <p>检索、筛选并查看演示情报的证据链。</p>
    </div>
    <div class="heading-count">
      共 <strong>{{ filtered.length }}</strong> 条结果
    </div>
  </div>
  <section class="panel list-panel" aria-label="情报列表">
    <IntelligenceFilters v-model:search="search" v-model:severity="severity" />
    <PageState
      :loading="store.loading || !store.initialized"
      :error="store.error"
      :empty="filtered.length === 0"
      empty-text="没有符合条件的演示情报，请调整搜索或筛选。"
      @retry="store.loadDemo"
    >
      <IntelligenceTable
        :rows="rows"
        :page="page"
        :page-count="pageCount"
        :total="filtered.length"
        @change-page="changePage"
      />
    </PageState>
  </section>
</template>
