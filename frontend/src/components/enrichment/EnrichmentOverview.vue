<script setup lang="ts">
import { shallowRef } from 'vue'
import type { IntelligenceItem } from '@/types'
import { enrichmentDimensions, type EnrichmentDimensionKey } from '@/config/intelligenceCategories'
import EnrichmentFields from './EnrichmentFields.vue'
defineProps<{ item: IntelligenceItem }>()
defineEmits<{ retry: [] }>()
const dimension = shallowRef<EnrichmentDimensionKey>('affected')
</script>
<template>
  <section class="panel enrichment-overview"><div class="panel-title"><div><h2>富化类别</h2><p>八个维度分类查看；来源字段与外部富化结果分别标记</p></div></div>
    <div class="enrichment-tabs" role="group" aria-label="详情富化类别"><button v-for="entry in enrichmentDimensions" :key="entry.key" type="button" :class="{ active: dimension === entry.key }" :aria-pressed="dimension === entry.key" @click="dimension = entry.key">{{ entry.name }}</button></div>
    <button v-if="item.structuredStatus === 'unavailable'" class="ghost-button" type="button" @click="$emit('retry')">重新加载富化信息</button>
    <EnrichmentFields :item="item" :snapshot="item.enrichment" :dimension="dimension" />
  </section>
</template>
