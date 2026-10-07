<script setup lang="ts">
defineProps<{ search: string; category: string; categories: string[]; count: number; total: number; focus: boolean }>()
defineEmits<{ 'update:search': [value: string]; 'update:category': [value: string]; 'update:focus': [value: boolean]; reset: [] }>()
</script>

<template>
  <div class="graph-toolbar">
    <label class="graph-search"><span>搜索实体</span><input type="search" :value="search" placeholder="输入 CVE 或实体名称…" @input="$emit('update:search', ($event.target as HTMLInputElement).value)" /></label>
    <label class="graph-category"><span>实体类别</span><select class="native-select" :value="category" @change="$emit('update:category', ($event.target as HTMLSelectElement).value)"><option value="all">全部类别</option><option v-for="name in categories" :key="name" :value="name">{{ name }}</option></select></label>
    <button type="button" class="graph-reset" @click="$emit('reset')">恢复视图</button>
    <label class="graph-focus"><input type="checkbox" :checked="focus" @change="$emit('update:focus', ($event.target as HTMLInputElement).checked)" />聚焦当前实体</label>
    <small>匹配 {{ count }} / {{ total }} 个实体</small>
  </div>
</template>

<style scoped>
.graph-toolbar { display: flex; align-items: flex-end; flex-wrap: wrap; gap: 10px; padding-bottom: 14px; }
.graph-search { flex: 1 1 220px; }
.graph-search span, .graph-category span { display: block; margin-bottom: 6px; font-size: 11px; color: #667085; }
.graph-search input { width: 100%; padding: 9px 11px; border: 1px solid #dce3ed; border-radius: 7px; font: inherit; font-size: 12px; background: #fff; color: #27354a; }
.graph-reset { border: 1px solid #dce3ed; border-radius: 7px; background: #fff; color: #44516a; padding: 9px 12px; cursor: pointer; font-size: 12px; }
.graph-toolbar small { width: 100%; color: #667085; font-size: 11px; }
.graph-focus { display: flex; align-items: center; gap: 6px; padding: 8px 0; color: #536078; font-size: 11px; }
.graph-reset:focus-visible, .graph-search input:focus-visible { outline: 2px solid #4262ed; outline-offset: 3px; }
</style>
