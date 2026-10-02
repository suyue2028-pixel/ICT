<script setup lang="ts">
import { shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'

const router = useRouter()
const query = shallowRef('')
function search() {
  void router.push({
    path: '/intelligence',
    query: query.value.trim() ? { q: query.value.trim() } : {},
  })
}
</script>

<template>
  <form class="workspace-search" role="search" @submit.prevent="search">
    <Search aria-hidden="true" />
    <label for="workspace-search" class="sr-only">搜索情报</label>
    <input
      id="workspace-search"
      v-model="query"
      type="search"
      placeholder="搜索 CVE、情报、产品或安全问题…"
    />
    <button type="submit" aria-label="搜索情报"><Search aria-hidden="true" /></button>
  </form>
</template>
