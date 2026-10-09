<script setup lang="ts">
import type { IntelligenceItem } from '@/types'
defineProps<{ item: IntelligenceItem }>()
</script>

<template>
  <div v-if="item.cvssVectors?.length || item.versionFacts?.length" class="source-facts">
    <section v-if="item.cvssVectors?.length" class="source-fact"><h3>来源记录的 CVSS 向量</h3><code v-for="vector in item.cvssVectors" :key="vector">{{ vector }}</code></section>
    <section v-for="(fact, index) in item.versionFacts" :key="index" class="source-fact">
      <h3>{{ fact.product }} <small>{{ fact.ecosystem }}</small></h3>
      <dl><div v-if="fact.fixed.length"><dt>来源记录的修复版本</dt><dd>{{ fact.fixed.join('、') }}</dd></div>
        <div v-if="fact.lastAffected.length"><dt>最后受影响版本</dt><dd>{{ fact.lastAffected.join('、') }}</dd></div>
        <div v-if="fact.introduced.length"><dt>引入标记</dt><dd>{{ fact.introduced.map(value => value === '0' ? '0（来源未指定起始版本）' : value).join('、') }}</dd></div>
      </dl>
    </section>
    <p>字段来自当前版本来源记录，具体适用范围请核对原始公告。</p>
  </div>
</template>

<style scoped>
.source-facts { margin-top: 18px; display: grid; gap: 12px; }
.source-fact { padding: 12px 14px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: 10px; }
.source-fact h3 { margin: 0 0 10px; color: #333333; font-size: 12px; }
.source-fact small { color: #6f6f6f; font-weight: 400; }
.source-fact code { display: block; overflow-wrap: anywhere; color: #505050; font-size: 11px; }
.source-fact dl { margin: 0; display: grid; gap: 6px; }
.source-fact dl div { display: grid; grid-template-columns: 1fr 1.4fr; gap: 12px; font-size: 12px; }
.source-fact dt { color: #6f6f6f; }
.source-fact dd { margin: 0; color: #333333; overflow-wrap: anywhere; }
.source-facts > p { margin: 0; color: #6f6f6f; font-size: 11px; line-height: 1.7; }
</style>
