<script setup lang="ts">
import { useRuntimeStatus } from '@/composables/useRuntimeStatus'
import { useIntelligenceStore } from '@/stores/intelligence'
const store = useIntelligenceStore()
const { runtime, index, checking, indexError, modeLabel, checkedTime, check } = useRuntimeStatus(() => store.isDemo)
</script>

<template>
  <section class="runtime-strip" aria-label="服务运行状态">
    <div class="runtime-top">
      <strong>本地联调</strong>
      <span :class="['runtime-badge', runtime.backend.state]">后端 · {{ runtime.backend.label }}</span>
      <span :class="['runtime-badge', runtime.agent.state]">Agent · {{ runtime.agent.label }}</span>
      <span class="runtime-mode">{{ modeLabel }}</span>
      <button type="button" class="runtime-refresh" :disabled="checking" @click="check">{{ checking ? '检查中…' : '检查状态' }}</button>
    </div>
    <div class="runtime-bottom">
      <span>{{ store.isDemo ? '虚构样本' : '真实资料' }}范围</span>
      <span v-if="index">索引事件：已索引 {{ index.indexed }} · 待处理 {{ index.pending }} · 失败 {{ index.failed }}</span>
      <span v-else>{{ indexError ? '索引状态无法获取' : '正在获取索引状态…' }}</span>
      <span v-if="checkedTime">检查于 {{ checkedTime }}</span>
      <span>虚构样本与真实资料分开</span>
    </div>
  </section>
</template>

<style scoped>
.runtime-strip { border: 1px solid #e4e9f1; background: #fff; border-radius: 10px; padding: 12px 16px; margin-bottom: 22px; }
.runtime-top, .runtime-bottom { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px; }
.runtime-top { font-size: 12px; color: #27354a; }
.runtime-bottom { margin-top: 9px; color: #667085; font-size: 11px; line-height: 1.6; }
.runtime-badge { padding: 4px 9px; border-radius: 6px; background: #f0f2f6; color: #667085; }
.runtime-badge.ready { color: #22664d; background: #eaf3ee; }
.runtime-badge.unavailable { color: #a6374d; background: #faebef; }
.runtime-mode { color: #536078; }
.runtime-refresh { margin-left: auto; border: 1px solid #dce3ed; border-radius: 6px; background: #fff; color: #44516a; padding: 5px 10px; cursor: pointer; }
.runtime-refresh:disabled { opacity: .6; cursor: wait; }
.runtime-refresh:focus-visible { outline: 2px solid #4262ed; outline-offset: 3px; }
</style>
