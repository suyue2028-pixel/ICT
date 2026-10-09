<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import type { IntelligenceItem } from '@/types'
import { severityNames } from '@/stores/intelligence'
const props = defineProps<{ items: IntelligenceItem[]; selectedId?: string }>()
defineEmits<{ select: [id: string] }>()
const page = shallowRef(1), pageSize = 5
const pages = computed(() => Math.max(1, Math.ceil(props.items.length / pageSize)))
const rows = computed(() => props.items.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(() => props.items, () => { page.value = 1 })
</script>
<template>
  <div class="document-choices">
    <article v-for="item in rows" :key="item.id" :class="['document-choice', { active: selectedId === item.id }]">
      <div class="choice-heading"><strong>{{ item.title }}</strong><span :class="['severity-badge', item.severity]">{{ severityNames[item.severity] }}</span></div>
      <p class="choice-meta">{{ item.source }} · {{ item.publishedAt || '发布日期暂无数据' }} · {{ item.cve || item.externalId || '编号暂无数据' }}</p>
      <div class="choice-actions"><button class="ghost-button" type="button" :aria-pressed="selectedId === item.id" @click="$emit('select', item.id)">查看关联内容</button><router-link :to="`/intelligence/${item.id}`">情报详情</router-link></div>
    </article>
    <p v-if="!items.length" class="choice-empty">暂无符合条件的已保存情报。</p>
    <nav v-if="items.length" class="choice-pagination" aria-label="分类情报分页"><span>{{ items.length }} 条情报 · 第 {{ page }} / {{ pages }} 页</span><div><button class="ghost-button" type="button" :disabled="page <= 1" @click="page--">上一页</button><button class="ghost-button" type="button" :disabled="page >= pages" @click="page++">下一页</button></div></nav>
  </div>
</template>
<style scoped>
.document-choices { display: grid; gap: 12px; }
.document-choice { padding: 16px; border: 1px solid #000; border-radius: 10px; min-width: 0; }
.document-choice.active { border-width: 2px; padding: 15px; }
.choice-heading { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.choice-heading strong { font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
.choice-heading .severity-badge { flex-shrink: 0; }
.choice-meta { font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; margin: 10px 0; }
.choice-actions, .choice-pagination, .choice-pagination > div { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.choice-actions { font-size: 12px; }
.choice-pagination { justify-content: space-between; font-size: 12px; margin-top: 8px; }
.choice-empty { padding: 25px 0; font-size: 13px; }
</style>
