<script setup lang="ts">
import { computed, useTemplateRef } from 'vue'
import { ChatDotRound, Clock, Right } from '@element-plus/icons-vue'
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
  <div class="app-shell">
    <header class="workspace-header">
      <router-link to="/" class="workspace-identity" aria-label="安全情报工作台首页">
        <span class="brand-mark mobile-brand" aria-hidden="true" translate="no">AI</span>
        <span class="brand-copy"
          ><strong>安全情报<span>工作台</span></strong
          ><small>智能体驱动的 AI 安全知识情报系统</small></span
        >
      </router-link>
      <WorkspaceSearch />
      <div class="header-sample-date">
        <Clock aria-hidden="true" /><span
          ><small>资料最新发布日期</small><time>{{ sampleDate }}</time></span
        >
      </div>
      <select class="native-select" aria-label="资料范围" :value="String(store.isDemo)" :disabled="store.loading" @change="store.setScope(($event.target as HTMLSelectElement).value === 'true')"><option value="false">真实资料</option><option value="true">虚构样本</option></select>
      <button type="button" class="demo-pill" :disabled="store.loading" @click="store.loadDemo">刷新资料</button>
      <WorkspaceNavigation mode="tabs" />
    </header>
    <aside ref="sidebar" class="sidebar" aria-label="工作台导航">
      <router-link to="/" class="sidebar-brand" aria-label="情报工作台首页">
        <span class="brand-mark" aria-hidden="true" translate="no">AI</span>
        <span>情报工作台</span>
      </router-link>
      <p class="sidebar-label">工作空间</p>
      <WorkspaceNavigation mode="rail" />
      <SidebarSwipeFeedback :feedback="feedback" />
      <router-link class="sidebar-assistant" to="/qa">
        <span class="assistant-icon"><ChatDotRound aria-hidden="true" /></span>
        <strong>可追溯问答</strong>
        <p>查看回答与情报引用<br />后端资料 · Agent 检索</p>
        <span class="assistant-arrow"><Right aria-hidden="true" /></span>
      </router-link>
    </aside>
    <div class="workspace">
      <main id="main-content" class="page-content" tabindex="-1">
        <div class="demo-notice" role="note">
          <span class="notice-icon" aria-hidden="true">i</span>
          <span
            ><strong>本地环境</strong> · 当前连接后端与 Agent。未配置大模型时使用资料摘录；虚构样本与真实资料分开。</span
          >
        </div>
        <slot />
      </main>
      <footer class="workspace-footer">安全情报工作台 <span>本地集成环境</span></footer>
    </div>
  </div>
</template>
