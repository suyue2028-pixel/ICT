<script setup lang="ts">
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import WorkspaceLayout from '@/components/layout/WorkspaceLayout.vue'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
const route = useRoute()
watch(() => [route.meta.layout, route.matched.length] as const, ([layout, matched]) => {
  if (matched && layout !== 'auth' && !store.initialized && !store.loading) void store.loadDemo()
}, { immediate: true })
</script>

<template>
  <router-view v-if="route.meta.layout === 'auth'" />
  <WorkspaceLayout v-else>
    <router-view />
  </WorkspaceLayout>
</template>
