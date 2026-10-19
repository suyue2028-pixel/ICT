<script setup lang="ts">
import { severityNames, severityOrder } from '@/stores/intelligence'
import type { Severity } from '@/types'

const search = defineModel<string>('search', { required: true })
const severity = defineModel<Severity | 'all'>('severity', { required: true })
</script>

<template>
  <div class="filter-bar">
    <label class="filter-search">
      <span class="sr-only">搜索情报</span>
      <el-input
        v-model="search"
        placeholder="搜索编号、标题、产品或关键词…"
        clearable
        size="large"
        class="list-search"
        name="intelligence-search"
        autocomplete="off"
      />
    </label>
    <label class="filter-severity">
      <span class="sr-only">风险等级</span>
      <select v-model="severity" class="native-select" name="severity">
        <option value="all">全部风险等级</option>
        <option v-for="key in severityOrder" :key="key" :value="key">
          {{ severityNames[key] }}
        </option>
      </select>
    </label>
  </div>
</template>
