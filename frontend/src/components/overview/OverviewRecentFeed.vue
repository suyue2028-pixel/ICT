<script setup lang="ts">
import type { IntelligenceItem } from '@/types'

defineProps<{ items: IntelligenceItem[] }>()
</script>

<template>
  <section class="panel intelligence-feed">
    <div class="panel-title">
      <h2>最近情报</h2>
      <router-link class="subtle-link" to="/intelligence">查看全部</router-link>
    </div>
    <div class="feed-list">
      <router-link
        v-for="item in items"
        :key="item.id"
        :to="`/intelligence/${item.id}`"
        class="feed-row"
      >
        <span :class="['source-tag', item.severity]">{{ item.source }}</span>
        <span class="feed-copy"
          ><strong>{{ item.title }}</strong
          ><small>{{ item.id }} · {{ item.publishedAt }}</small></span
        >
      </router-link>
    </div>
    <p v-if="!items.length" class="overview-empty">暂无数据</p>
  </section>
</template>
