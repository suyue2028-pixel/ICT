<script setup lang="ts">
import { computed } from 'vue'
import type { IntelligenceItem } from '@/types'
import type { Source } from '@/api/backend'
import { sourceCategories } from '@/config/intelligenceCategories'
import { publicUrl } from '@/api/paperEnrichment'
const props = defineProps<{ item: IntelligenceItem; items: IntelligenceItem[]; sources: Source[] }>()
const source = computed(() => props.sources.find(row => row.id === props.item.sourceId))
const category = computed(() => sourceCategories.find(row => row.key === source.value?.category)?.name || (source.value?.category === 'offline' ? '独立离线样本' : '暂无分类数据'))
const related = computed(() => props.item.cve ? props.items.filter(row => row.cve?.toUpperCase() === props.item.cve?.toUpperCase()) : [])
const url = computed(() => publicUrl(props.item.evidence[0]?.url))
</script>
<template>
  <section class="panel source-relations" aria-label="来源关系">
    <div class="panel-title"><div><h2>来源关系</h2><p>类别 → 来源 → 来源记录；同一 CVE 的资料关联</p></div></div>
    <h3 class="relation-title">{{ item.title }}</h3>
    <dl class="relation-fields"><div><dt>来源类别</dt><dd>{{ category }}</dd></div><div><dt>来源名称</dt><dd>{{ source?.name || item.source }}</dd></div><div><dt>来源编号</dt><dd>{{ item.externalId || '暂无数据' }}</dd></div><div><dt>CVE</dt><dd>{{ item.cve || '暂无数据' }}</dd></div><div><dt>原文链接</dt><dd><a v-if="url" :href="url" target="_blank" rel="noopener noreferrer">{{ url }}</a><span v-else>暂无数据</span></dd></div></dl>
    <h3 class="relation-title">同一 CVE 的来源记录（{{ related.length }}）</h3>
    <p v-if="!item.cve" class="relation-note">暂无 CVE，未建立同一漏洞的来源关联。</p>
    <div v-else class="relation-records"><router-link v-for="record in related" :key="record.id" :to="{ path: `/intelligence/${record.id}`, query: { content_hash: record.contentHash } }"><strong>{{ record.source }} · {{ record.externalId || record.id }}</strong><span>{{ record.title }}</span></router-link></div>
    <p class="relation-note">关联依据是来源记录中的同一 CVE 编号；多条记录不等于多个独立来源或独立核验。</p>
  </section>
</template>
<style scoped>
.relation-title { font-size: 15px; line-height: 1.7; margin: 0 0 16px; overflow-wrap: anywhere; }
.relation-fields { display: grid; gap: 15px; margin: 0 0 24px; }
.relation-fields > div { display: grid; grid-template-columns: 95px minmax(0, 1fr); gap: 12px; font-size: 12px; }
.relation-fields dd { margin: 0; overflow-wrap: anywhere; }
.relation-records { display: grid; gap: 10px; }
.relation-records a { border: 1px solid #000; padding: 12px; border-radius: 8px; display: grid; gap: 8px; font-size: 12px; overflow-wrap: anywhere; }
.relation-note { font-size: 11px; line-height: 1.8; margin-top: 18px; }
</style>
