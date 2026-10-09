<script setup lang="ts">
import { severityNames } from '@/stores/intelligence'
import type { IntelligenceItem } from '@/types'

defineProps<{ rows: IntelligenceItem[]; page: number; pageCount: number; total: number }>()
const emit = defineEmits<{ changePage: [page: number] }>()
</script>

<template>
  <div class="table-scroll">
    <table class="data-table">
      <caption class="sr-only">
        情报列表，共
        {{
          total
        }}
        条筛选结果
      </caption>
      <thead>
        <tr>
          <th scope="col">情报</th>
          <th scope="col">风险</th>
          <th scope="col">来源</th>
          <th scope="col">发布日期</th>
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in rows" :key="item.id">
          <td>
            <router-link class="table-title" :to="`/intelligence/${item.id}`">{{
              item.title
            }}</router-link>
            <div class="table-sub">
              {{ item.id }} · {{ item.products.join('、') || '暂无数据' }}
            </div>
          </td>
          <td>
            <span :class="['severity-badge', item.severity]">{{
              severityNames[item.severity]
            }}</span>
          </td>
          <td>{{ item.source }}</td>
          <td>
            <time :datetime="item.publishedAt">{{ item.publishedAt }}</time>
          </td>
          <td>
            <router-link
              class="row-arrow"
              :to="`/intelligence/${item.id}`"
              :aria-label="`查看${item.id}详情`"
              >详情</router-link
            >
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <nav class="pagination-row" aria-label="情报分页">
    <span>第 {{ page }} / {{ pageCount }} 页</span>
    <div>
      <button
        type="button"
        class="ghost-button"
        :disabled="page === 1"
        @click="emit('changePage', page - 1)"
      >
        上一页</button
      ><button
        type="button"
        class="ghost-button"
        :disabled="page >= pageCount"
        @click="emit('changePage', page + 1)"
      >
        下一页
      </button>
    </div>
  </nav>
</template>
