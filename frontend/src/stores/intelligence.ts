import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'
import { apiGet, errorMessage } from '@/api/http'
import { allPages, documentItem, enrichedItem } from '@/api/backend'
import type { Document, Enrichment, Source, Stats, Task, Vulnerability, Relations } from '@/api/backend'
import type { GraphLink, GraphNode, IntelligenceItem, Severity, TaskRecord } from '@/types'
export const severityNames: Record<Severity, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危', unknown: '未知' }
export const severityOrder: Severity[] = ['critical', 'high', 'medium', 'low', 'unknown']
export const useIntelligenceStore = defineStore('intelligence', () => {
  const items = shallowRef<IntelligenceItem[]>([])
  const loading = shallowRef(false), error = shallowRef(''), initialized = shallowRef(false)
  const isDemo = shallowRef(false), sources = shallowRef<Source[]>([])
  const stats = shallowRef<Stats | null>(null)
  const tasks = shallowRef<TaskRecord[]>([])
  const graphNodes = shallowRef<GraphNode[]>([]), graphLinks = shallowRef<GraphLink[]>([])
  async function loadDemo() {
    if (loading.value) return
    loading.value = true; error.value = ''
    try {
      const [docs, sourceRows, taskRows, statistics] = await Promise.all([
        allPages<Document>('/documents', isDemo.value), apiGet<Source[]>('/sources'),
        allPages<Task>('/tasks', isDemo.value), apiGet<Stats>('/stats', { is_demo: isDemo.value }),
      ])
      sources.value = sourceRows; items.value = docs.map(doc => documentItem(doc, sourceRows)); stats.value = statistics
      tasks.value = taskRows.map(task => ({ id: task.id, stage: '采集',
        name: sourceRows.find(x => x.id === task.source_id)?.name || task.kind,
        status: task.status === 'succeeded' ? '已完成' : task.status === 'failed' ? '失败' : '待处理',
        time: task.finished_at || task.created_at, createdAt: task.created_at, finishedAt: task.finished_at || undefined,
        detail: JSON.stringify(task.result), error: task.error_code || undefined }))
      initialized.value = true; graphNodes.value = []; graphLinks.value = []
    } catch (cause) { error.value = errorMessage(cause) }
    finally { loading.value = false }
  }
  async function loadGraph() {
    const scope = isDemo.value
    const vulnerabilities = await allPages<Vulnerability>('/intelligence', scope)
    const nodes = new Map<string, GraphNode>(), links: GraphLink[] = []
    for (const vuln of vulnerabilities.slice(0, 30)) {
      const graph = await apiGet<Relations>(`/relations/${vuln.id}`)
      // Source documents can themselves have document_type="vulnerability".
      // Their role is identified by document evidence IDs, not that content type.
      const documentIds = new Set(graph.edges.map(edge => edge.evidence.document_id))
      for (const node of graph.nodes) nodes.set(node.id, { id: node.id, name: node.label,
        category: documentIds.has(node.id) ? '情报' : '漏洞',
        description: documentIds.has(node.id) ? '后端保存的来源文档，与漏洞通过明确证据关联' : '后端记录的去重漏洞实体与来源关联',
        intelligenceIds: [...new Set(graph.edges.filter(x => x.from === node.id || x.to === node.id).map(x => x.evidence.document_id))] })
      links.push(...graph.edges.map(x => ({ source: x.from, target: x.to, relation: '来源记录' })))
    }
    if (scope === isDemo.value) { graphNodes.value = [...nodes.values()]; graphLinks.value = [...new Map(links.map(link => [`${link.source}:${link.target}:${link.relation}`, link])).values()] }
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
  async function setScope(demo: boolean) { isDemo.value = demo; items.value = []; tasks.value = []; stats.value = null; graphNodes.value = []; graphLinks.value = []; await loadDemo() }
  const latest = computed(() => [...items.value].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)))
  const findById = (id: string) => items.value.find(item => item.id === id)
  return { items, loading, error, initialized, isDemo, sources, stats, tasks, graphNodes, graphLinks,
    latest, findById, loadDemo, loadGraph, loadDetail, setScope }
})
