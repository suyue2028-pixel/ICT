<script setup lang="ts">
import { shallowRef } from 'vue'
import { useIntelligenceStore } from '@/stores/intelligence'
import SourceCategoryView from '@/components/classification/SourceCategoryView.vue'
import EnrichmentCategoryView from '@/components/classification/EnrichmentCategoryView.vue'
import PageState from '@/components/PageState.vue'
const store = useIntelligenceStore()
const mode = shallowRef<'sources' | 'enrichment'>('sources')
</script>
<template>
  <div class="page-heading"><div><h1>情报分类与来源关系</h1><p>按七类来源和八类富化维度查看情报，核对原始记录与引用证据。</p></div></div>
  <div class="classification-modes" role="group" aria-label="分类查看方式"><button type="button" :class="{ active: mode === 'sources' }" :aria-pressed="mode === 'sources'" @click="mode = 'sources'">来源分类</button><button type="button" :class="{ active: mode === 'enrichment' }" :aria-pressed="mode === 'enrichment'" @click="mode = 'enrichment'">富化分类</button></div>
  <PageState :loading="store.loading || !store.initialized" :error="store.error" @retry="store.loadDemo"><SourceCategoryView v-if="mode === 'sources'" /><EnrichmentCategoryView v-else /></PageState>
</template>
