<script setup lang="ts">
defineProps<{ loading?: boolean; error?: string; empty?: boolean; emptyText?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="error" class="page-state error-state" role="alert">
    {{ error }} <button class="text-button" @click="$emit('retry')">重试</button>
  </div>
  <div v-else-if="loading" class="page-state" role="status">
    <span class="spinner" /> 正在加载演示数据…
  </div>
  <div v-else-if="empty" class="page-state">{{ emptyText || '暂无数据' }}</div>
  <slot v-else />
</template>
