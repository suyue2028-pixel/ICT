<script setup lang="ts">
import { computed } from 'vue'
import { publicUrl } from '@/api/paperEnrichment'
import type { IntelligenceItem, PaperEnrichment } from '@/types'

const props = defineProps<{ item: IntelligenceItem }>()
const emit = defineEmits<{ retry: [] }>()
const methods: Record<string, string> = {
  source_structured: '来源结构化字段', source_link: '来源链接', rule: '规则提取', model: '模型提取',
}
const states: Record<string, string> = {
  known: '有来源依据', unknown: '暂无数据', unprocessed: '尚未进行论文富化',
  not_found: '未找到相关论文', failed: '论文富化失败', pending_review: '待审核',
}
const papers = computed<PaperEnrichment[]>(() => {
  if (props.item.paperEnrichments?.length) return props.item.paperEnrichments
  if (props.item.documentType !== 'paper') return []
  return [{ title: props.item.title, url: publicUrl(props.item.evidence[0]?.url),
    enrichmentType: '论文关联', state: 'unknown', quotes: [] }]
})
const status = computed(() => {
  if (props.item.structuredStatus === 'unavailable') return '论文富化信息获取失败，可重试加载详情。'
  if (props.item.structuredStatus === 'historical') return '暂无数据，当前引用版本未提供对应的论文富化信息。'
  const runs = props.item.paperEnrichmentRuns || []
  if (!runs.length) return '暂无数据，接口未提供论文富化结果。'
  return runs.map(run => `${run.provider ? `${run.provider}：` : ''}${states[run.state] || '状态未确认'}`).join('；')
})
function reviewLabel(paper: PaperEnrichment): string {
  if (paper.state === 'pending_review' || paper.reviewState === 'pending_review' || paper.method === 'model') return '待审核'
  return states[paper.state] || '状态未确认'
}
</script>

<template>
  <section class="panel paper-enrichment-panel" aria-labelledby="paper-enrichment-heading">
    <div class="panel-title">
      <div>
        <h2 id="paper-enrichment-heading">论文富化</h2>
        <p>论文题目、相关关键语句与富化类型</p>
      </div>
    </div>
    <p v-if="!papers.length" class="paper-empty">暂无数据</p>
    <p class="paper-status">{{ status }}</p>
    <button v-if="item.structuredStatus === 'unavailable'" type="button" class="ghost-button" @click="emit('retry')">重新加载富化信息</button>
    <div v-if="papers.length" class="paper-list">
      <article v-for="(paper, index) in papers" :key="`${paper.url || 'paper'}:${index}`" class="paper-card">
        <div class="paper-heading">
          <div>
            <span class="paper-label">论文题目</span>
            <h3 class="paper-title">{{ paper.title || '暂无数据' }}</h3>
          </div>
          <span class="paper-review">{{ reviewLabel(paper) }}</span>
        </div>
        <dl class="paper-fields">
          <div>
            <dt>富化类型</dt>
            <dd>{{ paper.state === 'unknown' ? '暂无数据' : paper.enrichmentType }}</dd>
          </div>
          <div>
            <dt>提取方式</dt>
            <dd>{{ paper.method ? methods[paper.method] || '暂无数据' : '暂无数据' }}</dd>
          </div>
        </dl>
        <div class="paper-quotes">
          <span class="paper-label">情报相关关键语句</span>
          <p v-if="!paper.quotes.length" class="paper-missing">暂无数据，未提供对应论文的引用片段。</p>
          <div v-for="(quote, quoteIndex) in paper.quotes" :key="quoteIndex" class="paper-quote">
            <blockquote class="paper-quote-text">{{ quote.excerpt }}</blockquote>
            <router-link :to="{ path: `/intelligence/${quote.documentId}`, query: { content_hash: quote.contentHash } }">
              查看引用版本
            </router-link>
            <small class="paper-locator">引用位置：{{ quote.locator }}</small>
          </div>
        </div>
        <a v-if="paper.url" class="paper-source" :href="paper.url" target="_blank" rel="noopener noreferrer">查看论文原始来源</a>
        <p v-else class="paper-missing">论文链接：暂无数据</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.paper-list { display: grid; gap: 18px; }
.paper-card { border-top: 1px solid #000000; padding-top: 20px; min-width: 0; }
.paper-heading { display: flex; justify-content: space-between; align-items: start; gap: 16px; }
.paper-label { display: block; font-size: 12px; margin-bottom: 8px; }
.paper-title { margin: 0; font-size: 17px; line-height: 1.6; overflow-wrap: anywhere; }
.paper-review { padding: 4px 9px; border: 1px solid #000000; border-radius: 6px; flex-shrink: 0; font-size: 11px; }
.paper-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 22px 0; }
.paper-fields dt { font-size: 12px; margin-bottom: 6px; }
.paper-fields dd { margin: 0; font-size: 14px; }
.paper-quote { margin-bottom: 16px; }
.paper-quote-text { margin: 0 0 10px; padding: 4px 0 4px 14px; border-left: 2px solid #000000; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; font-size: 14px; }
.paper-locator { display: block; margin-top: 7px; overflow-wrap: anywhere; font-size: 11px; }
.paper-status, .paper-empty, .paper-missing { line-height: 1.8; font-size: 13px; }
.paper-status { margin: 0 0 20px; }
.paper-source { display: inline-block; margin-top: 8px; }
@media (max-width: 700px) {
  .paper-heading { flex-direction: column; gap: 10px; }
}
</style>
