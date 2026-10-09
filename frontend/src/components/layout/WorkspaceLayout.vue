<script setup lang="ts">
import { computed, useTemplateRef } from 'vue'
import { useIntelligenceStore } from '@/stores/intelligence'
import WorkspaceNavigation from './WorkspaceNavigation.vue'
import WorkspaceSearch from './WorkspaceSearch.vue'
import SidebarSwipeFeedback from './SidebarSwipeFeedback.vue'
import { useSidebarSwipeNavigation } from '@/composables/useSidebarSwipeNavigation'

const store = useIntelligenceStore()
const sampleDate = computed(() => store.latest[0]?.publishedAt || '暂无数据')
const sidebar = useTemplateRef<HTMLElement>('sidebar')
const { feedback } = useSidebarSwipeNavigation(sidebar)
</script>

<template>
  <a class="skip-link" href="#main-content">跳转到主要内容</a>
  <div class="app-shell conversation-theme">
    <header class="workspace-header">
      <router-link to="/" class="workspace-identity" aria-label="安全情报工作台首页">
        <span class="brand-copy"
          ><strong>安全情报工作台</strong
          ><small>查找安全资料，核对来源与引用</small></span
        >
      </router-link>
      <WorkspaceSearch />
      <div class="header-sample-date">
        <span
          ><small>资料最新发布日期</small><time>{{ sampleDate }}</time></span
        >
      </div>
      <select class="native-select" aria-label="资料范围" :value="String(store.isDemo)" :disabled="store.loading" @change="store.setScope(($event.target as HTMLSelectElement).value === 'true')"><option value="false">真实资料</option><option value="true">虚构样本</option></select>
      <button type="button" class="demo-pill" :disabled="store.loading" @click="store.loadDemo">刷新资料</button>
      <WorkspaceNavigation mode="tabs" />
    </header>
    <aside ref="sidebar" class="sidebar" aria-label="工作台导航">
      <router-link to="/" class="sidebar-brand" aria-label="情报工作台首页">
        <span>安全情报</span>
      </router-link>
      <p class="sidebar-label">导航</p>
      <WorkspaceNavigation mode="rail" />
      <SidebarSwipeFeedback :feedback="feedback" />
      <p class="sidebar-note">检索资料<br />核对原文与引用</p>
    </aside>
    <div class="workspace">
      <main id="main-content" class="page-content" tabindex="-1">
        <slot />
      </main>
      <footer class="workspace-footer">安全情报工作台 <span>安全资料与来源检索</span></footer>
    </div>
  </div>
</template>
