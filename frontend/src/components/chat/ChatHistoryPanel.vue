<script setup lang="ts">
import { shallowRef } from 'vue'
import type { HistorySession } from '@/api/chat'

defineProps<{ sessions: HistorySession[]; activeId: string | null; loading: boolean; error: string;
  hasMore: boolean; disabled: boolean; authorized: boolean }>()
const emit = defineEmits<{ select: [id: string]; more: []; refresh: [] }>()
const expanded = shallowRef(!window.matchMedia('(max-width: 760px)').matches)
const date = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
function dateLabel(value: string) {
  return Number.isFinite(Date.parse(value)) ? date.format(new Date(value)) : '时间暂无数据'
}
function toggle(event: Event) { expanded.value = (event.currentTarget as HTMLDetailsElement).open }
</script>

<template>
  <aside class="qa-history" aria-label="历史会话面板">
    <details class="history-panel" :open="expanded" @toggle="toggle">
      <summary class="history-heading">历史会话 <small>{{ sessions.length }} 条已载入</small></summary>
      <div class="history-content">
        <p class="history-hint">新会话会保留旧记录，点击标题继续对话。</p>
        <p v-if="!authorized" class="history-status">授权后可查看你的历史会话。</p>
        <template v-else>
          <p v-if="error" class="history-status" role="alert">{{ error }}</p>
          <p v-else-if="loading && !sessions.length" class="history-status" role="status">正在读取历史会话…</p>
          <p v-else-if="!sessions.length" class="history-status">暂无历史会话。发送第一个问题后会自动保存。</p>
          <nav v-if="sessions.length" class="history-list" aria-label="已保存的历史会话">
            <button v-for="session in sessions" :key="session.session_id" type="button"
              :data-session-id="session.session_id"
              :class="['history-entry', { active: activeId === session.session_id }]"
              :aria-current="activeId === session.session_id ? 'true' : undefined"
              :aria-label="`打开历史会话：${session.title}`" :disabled="disabled"
              @click="emit('select', session.session_id)">
              <strong class="history-title">{{ session.title }}</strong>
              <span class="history-preview">{{ session.preview }}</span>
              <span class="history-meta"><time :datetime="session.updated_at">{{ dateLabel(session.updated_at) }}</time><span>{{ session.turn_count }} 轮</span></span>
            </button>
          </nav>
          <div class="history-actions">
            <button type="button" :disabled="loading || disabled" @click="emit('refresh')">刷新记录</button>
            <button v-if="hasMore" type="button" :disabled="loading || disabled" @click="emit('more')">{{ loading ? '读取中…' : '更早的会话' }}</button>
          </div>
        </template>
      </div>
    </details>
  </aside>
</template>

<style scoped>
.qa-history { align-self: start; position: sticky; top: 18px; min-width: 0; }
.history-panel { border: 1px solid #000; border-radius: 16px; background: var(--panel-background); overflow: hidden; }
.history-heading { padding: 18px 16px; font-size: 15px; font-weight: 600; cursor: pointer; }
.history-heading small { margin-left: 7px; font-size: 10px; font-weight: 400; white-space: nowrap; }
.history-content { padding: 0 12px 12px; }
.history-hint, .history-status { margin: 0 4px 13px; font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; }
.history-list { display: grid; gap: 8px; max-height: 390px; overflow-y: auto; overscroll-behavior: contain; padding: 2px; scrollbar-width: thin; }
.history-entry { display: block; width: 100%; padding: 12px; text-align: left; border: 1px solid transparent; border-radius: 12px; color: #000; background: var(--panel-background); cursor: pointer; }
.history-entry:hover, .history-entry.active { border-color: #000; }
.history-entry.active { box-shadow: inset 3px 0 0 #000; }
.history-title, .history-preview { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; }
.history-title { font-size: 12px; line-height: 1.6; font-weight: 550; }
.history-preview { font-size: 11px; line-height: 1.7; margin-top: 6px; }
.history-meta { display: flex; justify-content: space-between; gap: 6px; margin-top: 8px; font-size: 10px; }
.history-actions { display: flex; gap: 8px; margin: 14px 2px 0; }
.history-actions button { border: 1px solid #000; border-radius: 8px; padding: 6px 9px; font-size: 11px; color: #000; background: var(--panel-background); cursor: pointer; }
.history-actions button:disabled, .history-entry:disabled { cursor: wait; opacity: .6; }
@media (max-width: 760px) { .qa-history { position: static; } .history-list { max-height: 250px; } }
</style>
