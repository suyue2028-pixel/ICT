<script setup lang="ts">
import { onMounted } from 'vue'
import WorkspaceLayout from '@/components/layout/WorkspaceLayout.vue'
import { useIntelligenceStore } from '@/stores/intelligence'

const store = useIntelligenceStore()
onMounted(() => store.loadDemo())
</script>

<template>
  <WorkspaceLayout>
    <router-view />
  </WorkspaceLayout>
</template>
