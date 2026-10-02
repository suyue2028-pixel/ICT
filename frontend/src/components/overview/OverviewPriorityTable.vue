<script setup lang="ts">
import { WarningFilled } from '@element-plus/icons-vue'
import { severityNames } from '@/stores/intelligence'
import type { IntelligenceItem } from '@/types'

defineProps<{ items: IntelligenceItem[] }>()
</script>

<template>
  <section class="panel priority-panel">
    <div class="panel-title">
      <h2><WarningFilled class="section-icon danger" aria-hidden="true" />重点风险情报</h2>
      <router-link class="subtle-link" to="/intelligence">查看更多 →</router-link>
    </div>
    <div v-if="items.length" class="table-scroll">
      <table class="data-table overview-table">
        <caption class="sr-only">
          当前时间范围内的严重及高危演示情报
        </caption>
        <thead>
          <tr>
            <th scope="col">编号 / CVE</th>
            <th scope="col">情报名称</th>
            <th scope="col">CVSS</th>
            <th scope="col">风险</th>
            <th scope="col">发布日期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>
              <router-link class="subtle-link" :to="`/intelligence/${item.id}`">{{
                item.id
              }}</router-link
              ><small class="table-sub">{{ item.cve || '暂无数据' }}</small>
            </td>
            <td>
              <router-link class="priority-title" :to="`/intelligence/${item.id}`">{{
                item.title
              }}</router-link
              ><small class="table-sub">{{ item.products.join('、') || '暂无数据' }}</small>
            </td>
            <td>{{ item.cvss ?? '暂无数据' }}</td>
            <td>
              <span :class="['severity-badge', item.severity]">{{
                severityNames[item.severity]
              }}</span>
            </td>
            <td>
              <time :datetime="item.publishedAt">{{ item.publishedAt }}</time>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="overview-empty">当前范围暂无严重或高危情报。</p>
  </section>
</template>
