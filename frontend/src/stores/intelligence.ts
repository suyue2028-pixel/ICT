import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'
import { apiGet, errorMessage } from '@/api/http'
import { allPages, documentItem, enrichedItem } from '@/api/backend'
import type { Document, Enrichment, Source, Stats } from '@/api/backend'
import type { IntelligenceItem, Severity } from '@/types'
export const severityNames: Record<Severity, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危', unknown: '未知' }
export const severityOrder: Severity[] = ['critical', 'high', 'medium', 'low', 'unknown']
export const useIntelligenceStore = defineStore('intelligence', () => {
  const items = shallowRef<IntelligenceItem[]>([])
  const loading = shallowRef(false), error = shallowRef(''), initialized = shallowRef(false)
  const isDemo = shallowRef(false), sources = shallowRef<Source[]>([])
  const stats = shallowRef<Stats | null>(null)
  async function loadDemo() {
    if (loading.value) return
    loading.value = true; error.value = ''
    try {
      const [docs, sourceRows, statistics] = await Promise.all([
        allPages<Document>('/documents', isDemo.value), apiGet<Source[]>('/sources'),
        apiGet<Stats>('/stats', { is_demo: isDemo.value }),
      ])
      sources.value = sourceRows; items.value = docs.map(doc => documentItem(doc, sourceRows)); stats.value = statistics
      initialized.value = true
    } catch (cause) { error.value = errorMessage(cause) }
    finally { loading.value = false }
  }
  async function loadDetail(id: string, hash?: string): Promise<IntelligenceItem> {
    const scope = isDemo.value
    let doc = await apiGet<Document>(`/documents/${id}`)
    const currentHash = doc.content_hash
    if (doc.is_demo !== scope || scope !== isDemo.value) throw new Error('该来源属于另一资料范围，请切换后查看。')
    if (hash && hash !== doc.content_hash) {
      const revision = doc.revisions?.find(row => row.content_hash === hash)?.payload
      if (!revision || typeof revision.content !== 'string' || typeof revision.title !== 'string'
          || typeof revision.source_url !== 'string') throw new Error('找不到引用的原始版本。')
      const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
      doc = { ...doc, title: revision.title, content: revision.content, excerpt: revision.content.slice(0, 240),
        source_url: revision.source_url, content_hash: hash,
        external_id: typeof revision.external_id === 'string' ? revision.external_id : '',
        document_type: typeof revision.document_type === 'string' ? revision.document_type : 'unknown',
        content_completeness: typeof revision.content_completeness === 'string' ? revision.content_completeness : 'unknown',
        published_at: typeof revision.published_at === 'string' ? revision.published_at : null,
        source_reported_severity: typeof revision.severity === 'string' ? revision.severity : null,
        source_products: strings(revision.products), source_aliases: strings(revision.aliases),
        raw_payload: { aliases: strings(revision.aliases), products: strings(revision.products),
          vulnerability_id: typeof revision.vulnerability_id === 'string' ? revision.vulnerability_id : undefined } }
    }
    const item = documentItem(doc, sources.value)
    if (hash && hash !== currentHash) return { ...item, structuredStatus: 'historical' }
    try {
      const enrichment = await apiGet<Enrichment>(`/documents/${id}/enrichment`)
      if (enrichment.content_hash !== item.contentHash) return { ...item, structuredStatus: 'historical' }
      return enrichedItem(item, enrichment)
    } catch { return { ...item, structuredStatus: 'unavailable' } }
  }
  async function setScope(demo: boolean) { isDemo.value = demo; items.value = []; stats.value = null; await loadDemo() }
  const latest = computed(() => [...items.value].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)))
  const findById = (id: string) => items.value.find(item => item.id === id)
  return { items, loading, error, initialized, isDemo, sources, stats,
    latest, findById, loadDemo, loadDetail, setScope }
})
