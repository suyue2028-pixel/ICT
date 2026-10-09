<script setup lang="ts">
import { computed } from 'vue'
import type { IntelligenceItem } from '@/types'
import type { EnrichmentSnapshot } from '@/types/enrichment'
import { enrichmentDimensions, type EnrichmentDimensionKey } from '@/config/intelligenceCategories'
import { dimensionBlocks, dimensionState, enrichmentStateNames } from '@/utils/enrichmentPresentation'
import PaperEnrichmentPanel from '@/components/intelligence/PaperEnrichmentPanel.vue'
import { enrichedItem } from '@/api/backend'
const props = defineProps<{ item: IntelligenceItem; snapshot?: EnrichmentSnapshot; dimension: EnrichmentDimensionKey }>()
const definition = computed(() => enrichmentDimensions.find(row => row.key === props.dimension)!)
const blocks = computed(() => dimensionBlocks(props.item, props.snapshot, props.dimension))
const state = computed(() => dimensionState(props.item, props.snapshot, props.dimension))
const paperItem = computed(() => {
  const snapshot = props.snapshot
  return snapshot && snapshot.content_hash === props.item.contentHash && (!snapshot.document_id || snapshot.document_id === props.item.id)
    ? enrichedItem(props.item, snapshot) : props.item
})
</script>
<template>
  <div class="enrichment-fields">
    <div class="enrichment-field-title"><h3>{{ definition.name }}</h3><span>{{ enrichmentStateNames[state] || '暂无数据' }}</span></div>
    <p class="enrichment-description">{{ definition.description }}</p>
    <p v-if="dimension === 'poc'" class="enrichment-description">仅展示来源提供的公开链接与元数据，未执行 POC。</p>
    <template v-if="dimension === 'papers'"><PaperEnrichmentPanel :item="paperItem" /></template>
    <template v-else>
      <article v-for="(block, index) in blocks" :key="index" class="enrichment-block">
        <div class="enrichment-block-title"><strong>{{ block.origin }}</strong><span>{{ block.state === 'pending_review' ? '待审核' : '已有记录' }}</span></div>
        <ul v-if="block.facts.length" class="enrichment-facts"><li v-for="(fact, factIndex) in block.facts" :key="factIndex">{{ fact }}</li></ul>
        <p v-else-if="!block.links.length">具体字段：暂无数据</p>
        <div v-if="block.links.length" class="enrichment-links"><a v-for="link in block.links" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.title }}</a></div>
        <div v-for="(quote, quoteIndex) in block.quotes.slice(0, 3)" :key="quoteIndex" class="enrichment-citation"><blockquote>{{ quote.text.slice(0, 240) }}{{ quote.text.length > 240 ? '…' : '' }}</blockquote><router-link :to="{ path: `/intelligence/${quote.documentId}`, query: { content_hash: quote.contentHash } }">查看引用版本</router-link><small>引用位置：{{ quote.locator }}</small></div>
        <p v-if="block.evidenceId" class="enrichment-evidence-id">来源证据标识：{{ block.evidenceId }}</p>
      </article>
      <p v-if="!blocks.length" class="enrichment-empty">暂无数据 · {{ enrichmentStateNames[state] || '状态未确认' }}</p>
      <p v-if="!blocks.length && dimension === 'affected' && item.products.length" class="enrichment-description">来源元数据中的产品：{{ item.products.join('、') }}。版本范围：暂无数据。</p>
    </template>
  </div>
</template>
<style scoped>
.enrichment-field-title, .enrichment-block-title { display: flex; justify-content: space-between; flex-wrap: wrap; align-items: baseline; gap: 12px; }
.enrichment-field-title h3 { margin: 0; font-size: 16px; }
.enrichment-field-title span, .enrichment-description, .enrichment-block-title { font-size: 12px; line-height: 1.8; }
.enrichment-description { margin: 10px 0 20px; }
.enrichment-block { border-top: 1px solid #000; padding: 18px 0; min-width: 0; }
.enrichment-block-title span { border: 1px solid #000; padding: 2px 7px; border-radius: 5px; }
.enrichment-facts { padding-left: 18px; font-size: 13px; line-height: 1.9; overflow-wrap: anywhere; }
.enrichment-links { display: grid; gap: 10px; font-size: 13px; margin-top: 12px; overflow-wrap: anywhere; }
.enrichment-links a { text-decoration: underline; }
.enrichment-citation { margin-top: 16px; font-size: 12px; }
.enrichment-citation blockquote { margin: 0 0 8px; border-left: 2px solid #000; padding-left: 12px; overflow-wrap: anywhere; line-height: 1.8; }
.enrichment-citation small { display: block; margin-top: 7px; overflow-wrap: anywhere; }
.enrichment-evidence-id { font-size: 10px; overflow-wrap: anywhere; margin-top: 14px; }
.enrichment-empty { font-size: 13px; padding: 16px 0; }
</style>
