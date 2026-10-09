<script setup lang="ts">
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { useIntelligenceStore } from '@/stores/intelligence'
import { apiGet } from '@/api/http'
import type { EnrichmentSnapshot } from '@/types/enrichment'
import { enrichmentDimensions, type EnrichmentDimensionKey } from '@/config/intelligenceCategories'
import { dimensionBlocks, dimensionState } from '@/utils/enrichmentPresentation'
import EnrichmentFields from '@/components/enrichment/EnrichmentFields.vue'
import DocumentChoices from './DocumentChoices.vue'
import PageState from '@/components/PageState.vue'
const store = useIntelligenceStore()
const dimension = shallowRef<EnrichmentDimensionKey>('affected'), filter = shallowRef('available'), search = shallowRef(''), selectedId = shallowRef('')
const snapshots = shallowRef<Map<string, EnrichmentSnapshot>>(new Map())
const loading = shallowRef(false), completed = shallowRef(0), failures = shallowRef(0)
let request = 0
onBeforeUnmount(() => { request++ })
async function load() {
  const version = ++request, items = [...store.items]
  loading.value = true; completed.value = 0; failures.value = 0
  const results = new Map<string, EnrichmentSnapshot>()
  let next = 0
  await Promise.all(Array.from({ length: Math.min(4, items.length) }, async () => {
    while (next < items.length && request === version) {
      const item = items[next++]!
      try {
        const snapshot = await apiGet<EnrichmentSnapshot>(`/documents/${item.id}/enrichment`)
        if (snapshot.content_hash === item.contentHash) results.set(item.id, snapshot)
        else if (request === version) failures.value++
      } catch { if (request === version) failures.value++ }
      finally { if (request === version) completed.value++ }
    }
  }))
  if (request === version) { snapshots.value = results; loading.value = false }
}
watch(() => store.items, () => { void load() }, { immediate: true })
const groups = computed(() => enrichmentDimensions.map(entry => ({ ...entry,
  count: store.items.filter(item => dimensionBlocks(item, snapshots.value.get(item.id), entry.key).length > 0).length,
})))
const records = computed(() => store.latest.filter(item => {
  const state = dimensionState(item, snapshots.value.get(item.id), dimension.value)
  const matches = filter.value === 'all' || filter.value === 'available' && ['known', 'pending_review'].includes(state)
    || filter.value === 'pending' && state === 'pending_review' || filter.value === 'missing' && !['known', 'pending_review'].includes(state)
  return matches && `${item.title} ${item.cve || ''} ${item.externalId || ''} ${item.products.join(' ')}`.toLowerCase().includes(search.value.trim().toLowerCase())
}))
const selected = computed(() => records.value.find(item => item.id === selectedId.value))
watch(records, items => { if (!items.some(item => item.id === selectedId.value)) selectedId.value = items[0]?.id || '' }, { immediate: true })
watch(dimension, () => { filter.value = 'available'; search.value = '' })
</script>
<template>
  <PageState :loading="loading" :error="!loading && failures > 0 && snapshots.size === 0 ? '富化记录读取失败，请重试。' : ''" @retry="load">
    <div class="classification-layout">
      <aside class="panel category-menu" aria-label="八类富化维度"><h2>富化分类</h2><button v-for="group in groups" :key="group.key" type="button" :class="['category-choice', { active: dimension === group.key }]" :aria-pressed="dimension === group.key" @click="dimension = group.key"><span>{{ group.name }}</span><small>{{ group.count }} 条已有记录</small></button><p>计数包含待审核记录，具体状态在内容中标明。</p></aside>
      <div class="classification-main"><section class="panel"><div class="panel-title"><div><h2>{{ enrichmentDimensions.find(entry => entry.key === dimension)?.name }}</h2><p>已读取 {{ completed }} / {{ store.items.length }} 条情报的当前版本富化记录</p></div><button class="ghost-button" type="button" @click="load">重新读取</button></div><p v-if="failures" class="classification-note">{{ failures }} 条记录读取失败或版本变化，未计入已有记录。</p>
        <div class="enrichment-filters"><label>记录状态<select v-model="filter" class="native-select"><option value="available">有记录（含待审核）</option><option value="all">全部记录</option><option value="pending">待审核</option><option value="missing">暂无或未处理</option></select></label><label class="classification-search"><span>搜索当前富化类别</span><input v-model="search" type="search" placeholder="输入 CVE、标题或产品…" /></label></div>
        <DocumentChoices :items="records" :selected-id="selectedId" @select="selectedId = $event" />
      </section><section v-if="selected" class="panel"><div class="panel-title"><div><h2>富化内容与证据</h2><p>{{ selected.title }}</p></div></div><EnrichmentFields :item="selected" :snapshot="snapshots.get(selected.id)" :dimension="dimension" /></section></div>
    </div>
  </PageState>
</template>
