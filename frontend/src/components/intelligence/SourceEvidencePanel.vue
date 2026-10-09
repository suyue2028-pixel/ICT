<script setup lang="ts">
import { computed } from 'vue'
import type { Evidence, IntelligenceItem } from '@/types'
import { publicUrl } from '@/api/paperEnrichment'
import { documentTypeNames, sourceExcerpts } from '@/utils/sourceEvidence'

const props = defineProps<{ item: IntelligenceItem }>()
const category = computed(() => documentTypeNames[props.item.documentType || ''] || '暂无数据')
const evidenceRows = computed(() => {
  const item = props.item
  const keywords = [item.cve || '', ...(item.cwe?.split('、') || []), ...item.products]
  const evidence: Evidence[] = item.evidence.length ? item.evidence : [{ title: item.title }]
  return evidence.map(entry => ({
    title: entry.title,
    url: publicUrl(entry.url),
    quotes: item.completeness === 'metadata_only' ? [] : sourceExcerpts(entry.excerpt || '', keywords),
  }))
})
</script>

<template>
  <section class="panel source-evidence-panel" aria-labelledby="source-evidence-heading">
    <div class="panel-title">
      <div>
        <h2 id="source-evidence-heading">来源与证据</h2>
        <p>当前引用版本的原文摘录，最多显示 3 段</p>
      </div>
    </div>
    <div class="source-evidence-list">
      <article v-for="(evidence, index) in evidenceRows" :key="index" class="source-evidence-card">
        <dl class="source-evidence-fields">
          <div class="source-evidence-field">
            <dt>类别</dt>
            <dd><span class="soft-tag">{{ category }}</span></dd>
          </div>
          <div class="source-evidence-field">
            <dt>关键字句</dt>
            <dd>
              <div v-if="evidence.quotes.length" class="source-excerpts">
                <blockquote v-for="(quote, quoteIndex) in evidence.quotes" :key="quoteIndex" class="source-excerpt">
                  <span v-if="quote.truncatedBefore" aria-hidden="true">…</span><span class="source-excerpt-text">{{ quote.text }}</span><span v-if="quote.truncatedAfter" aria-hidden="true">…</span>
                </blockquote>
              </div>
              <p v-else class="source-evidence-empty">暂无数据</p>
            </dd>
          </div>
          <div class="source-evidence-field">
            <dt>原文链接</dt>
            <dd>
              <a v-if="evidence.url" class="source-evidence-link" :href="evidence.url"
                :aria-label="`查看原文：${evidence.title || '来源资料'}`" target="_blank" rel="noopener noreferrer">{{ evidence.url }}</a>
              <p v-else class="source-evidence-empty">暂无数据</p>
            </dd>
          </div>
        </dl>
      </article>
    </div>
  </section>
</template>

<style scoped>
.source-evidence-card + .source-evidence-card { border-top: 1px solid #000000; margin-top: 22px; padding-top: 22px; }
.source-evidence-fields { display: grid; gap: 22px; margin: 0; }
.source-evidence-field { min-width: 0; }
.source-evidence-field dt { font-size: 12px; margin-bottom: 10px; font-weight: 600; }
.source-evidence-field dd { margin: 0; min-width: 0; }
.source-excerpts { display: grid; gap: 14px; }
.source-excerpt { margin: 0; border-left: 2px solid #000000; padding-left: 14px; color: #000000; font-size: 14px; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.source-evidence-link { color: #000000; font-size: 13px; line-height: 1.8; text-decoration: underline; overflow-wrap: anywhere; }
.source-evidence-empty { margin: 0; color: #000000; font-size: 13px; }
</style>
