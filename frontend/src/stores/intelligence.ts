import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'
import {
  sampleGraphLinks,
  sampleGraphNodes,
  sampleIntelligence,
  sampleTasks,
} from '@/mock/intelligence'
import type { IntelligenceItem, Severity } from '@/types'

export const severityNames: Record<Severity, string> = {
  critical: '严重',
  high: '高危',
  medium: '中危',
  low: '低危',
}
export const severityOrder: Severity[] = ['critical', 'high', 'medium', 'low']

export const useIntelligenceStore = defineStore('intelligence', () => {
  const items = shallowRef<IntelligenceItem[]>([])
  const loading = shallowRef(false)
  const error = shallowRef('')
  const initialized = shallowRef(false)

  async function loadDemo() {
    if (loading.value) return
    loading.value = true
    error.value = ''
    try {
      await new Promise((resolve) => setTimeout(resolve, 220))
      items.value = sampleIntelligence
      initialized.value = true
    } catch {
      error.value = '演示数据加载失败，请重试。'
    } finally {
      loading.value = false
    }
  }

  const latest = computed(() =>
    [...items.value].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)),
  )
  const graphNodes = sampleGraphNodes
  const graphLinks = sampleGraphLinks
  const tasks = sampleTasks
  const findById = (id: string) => items.value.find((item) => item.id === id)

  return {
    items,
    loading,
    error,
    initialized,
    latest,
    graphNodes,
    graphLinks,
    tasks,
    findById,
    loadDemo,
  }
})
