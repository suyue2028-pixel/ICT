<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useIntelligenceStore } from '@/stores/intelligence'
import { sourceCategories, sourceDisplayNames } from '@/config/intelligenceCategories'
import DocumentChoices from './DocumentChoices.vue'
import SourceRelations from './SourceRelations.vue'
const store = useIntelligenceStore(), route = useRoute()
const category = shallowRef('vulnerability'), sourceId = shallowRef(''), search = shallowRef(''), selectedId = shallowRef('')
const groups = computed(() => {
  const standard = sourceCategories.map(group => {
    const sources = store.sources.filter(source => source.category === group.key)
    const ids = new Set(sources.map(source => source.id))
    return { ...group, sources, count: store.items.filter(item => ids.has(item.sourceId || '')).length }
  })
  const other = store.sources.filter(source => !sourceCategories.some(group => group.key === source.category))
  if (store.isDemo || other.some(source => store.items.some(item => item.sourceId === source.id))) {
    const ids = new Set(other.map(source => source.id))
    return [...standard, { key: 'other', name: '独立样本及其他来源', sources: other, count: store.items.filter(item => ids.has(item.sourceId || '')).length }]
  }
  return standard
})
const selectedGroup = computed(() => groups.value.find(group => group.key === category.value) || groups.value[0]!)
const records = computed(() => {
  const ids = new Set(selectedGroup.value.sources.map(source => source.id))
  const needle = search.value.trim().toLowerCase()
  return store.latest.filter(item => ids.has(item.sourceId || '') && (!sourceId.value || sourceId.value === item.sourceId)
    && `${item.title} ${item.cve || ''} ${item.externalId || ''} ${item.summary} ${item.products.join(' ')}`.toLowerCase().includes(needle))
})
const selected = computed(() => records.value.find(item => item.id === selectedId.value))
watch(records, rows => { if (!rows.some(item => item.id === selectedId.value)) selectedId.value = rows[0]?.id || '' }, { immediate: true })
function selectCategory(key: string) { category.value = key; sourceId.value = ''; search.value = '' }
watch(() => store.isDemo, () => { search.value = '' })
watch(() => [store.isDemo, store.sources, route.query.source_id], () => {
  const requested = store.sources.find(source => source.id === route.query.source_id && (store.isDemo ? source.category === 'offline' : source.category !== 'offline'))
  category.value = requested && sourceCategories.some(group => group.key === requested.category) ? requested.category : store.isDemo ? 'other' : 'vulnerability'
  sourceId.value = requested?.id || ''
}, { immediate: true })
</script>
<template>
  <div class="classification-layout">
    <aside class="panel category-menu" aria-label="七类情报来源"><h2>来源分类</h2><button v-for="group in groups" :key="group.key" type="button" :class="['category-choice', { active: category === group.key }]" :aria-pressed="category === group.key" @click="selectCategory(group.key)"><span>{{ group.name }}</span><small>{{ group.count }} 条情报</small></button><p>离线演示样本独立展示，不计入七类来源。</p></aside>
    <div class="classification-main">
      <section class="panel source-category-panel"><div class="panel-title"><div><h2>{{ selectedGroup.name }}</h2><p>当前配置 {{ selectedGroup.sources.length }} 个来源；已保存 {{ selectedGroup.count }} 条情报</p></div></div>
        <div class="source-choice-list"><button v-for="source in selectedGroup.sources" :key="source.id" type="button" :aria-pressed="sourceId === source.id" :class="['source-choice', { active: sourceId === source.id }]" @click="sourceId = sourceId === source.id ? '' : source.id"><strong>{{ sourceDisplayNames[source.adapter] || source.name }}</strong><span>{{ store.items.filter(item => item.sourceId === source.id).length }} 条已保存情报</span><small>{{ source.enabled ? '自动采集已启用' : '自动采集关闭' }}</small></button><p v-if="!selectedGroup.sources.length">暂无配置来源。</p></div>
        <p class="classification-note">来源已配置不代表已有采集数据。再次点击来源可取消筛选。</p>
        <label class="classification-search"><span>搜索当前分类情报</span><input v-model="search" type="search" placeholder="输入 CVE、标题、产品或关键词…" /></label>
        <DocumentChoices :items="records" :selected-id="selectedId" @select="selectedId = $event" />
      </section>
      <SourceRelations v-if="selected" :item="selected" :items="store.items" :sources="store.sources" />
    </div>
  </div>
</template>
