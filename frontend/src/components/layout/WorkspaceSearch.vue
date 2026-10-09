<script setup lang="ts">
import { shallowRef } from 'vue'
import { useRouter } from 'vue-router'

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
    <label for="workspace-search" class="sr-only">搜索情报</label>
    <input
      id="workspace-search"
      v-model="query"
      type="search"
      placeholder="搜索 CVE、产品或情报标题"
    />
    <button type="submit" aria-label="搜索情报">搜索</button>
  </form>
</template>
