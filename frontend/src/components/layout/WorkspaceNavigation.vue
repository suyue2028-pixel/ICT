<script setup lang="ts">
import { useRoute } from 'vue-router'
import { isWorkspacePage, workspacePages } from '@/navigation/workspacePages'

defineProps<{ mode: 'rail' | 'tabs' }>()
const route = useRoute()
function isCurrent(path: string) {
  return isWorkspacePage(route.path, path)
}
</script>

<template>
  <nav :class="['workspace-nav', mode]" aria-label="主导航">
    <router-link
      v-for="page in workspacePages"
      :key="page.to"
      :to="page.to"
      :class="['workspace-nav-link', { active: isCurrent(page.to) }]"
      :aria-current="isCurrent(page.to) ? 'page' : undefined"
      :title="mode === 'rail' ? page.label : undefined"
    >
      <component :is="page.icon" v-if="mode === 'rail'" class="nav-icon" aria-hidden="true" />
      <span>{{ page.label }}</span>
    </router-link>
  </nav>
</template>
