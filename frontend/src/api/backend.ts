import { apiGet } from './http'
import type { IntelligenceItem, Severity } from '@/types'
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Source { id: string; name: string; category: string; adapter: string; enabled: boolean }
export interface Document {
  id: string; source_id: string; external_id: string; title: string; source_url: string;
  document_type: string; content_hash: string; is_demo: boolean; published_at: string | null;
  first_collected_at: string; ai_relevant: boolean; content_completeness: string;
  source_reported_severity: string | null; excerpt: string; content?: string; source_products?: string[]; source_aliases?: string[];
  revisions?: { content_hash: string; payload: Record<string, unknown> }[];
  raw_payload?: { aliases?: string[]; vulnerability_id?: string; products?: string[] };
}
export interface Task { id: string; source_id: string; kind: string; status: string;
  error_code: string | null; created_at: string; finished_at: string | null; result: Record<string, unknown> }
export interface Vulnerability { id: string; identifier: string; title: string; severity: string }
export interface Relations { nodes: { id: string; type: string; label: string }[];
  edges: { from: string; to: string; type: string; evidence: { document_id: string; content_hash: string; source_url: string } }[] }
export interface Stats { documents: number; vulnerabilities: number; category_count: number }
export async function allPages<T>(path: string, isDemo: boolean) {
  const items: T[] = []
  for (let page = 1; page <= 100; page++) {
    const result = await apiGet<Page<T>>(path, { is_demo: isDemo, page, page_size: 100 })
    items.push(...result.items)
    if (items.length >= result.total) return items
  }
  throw new Error('Record pagination limit reached')
}
export function documentItem(doc: Document, sources: Source[]): IntelligenceItem {
  const level = doc.source_reported_severity || 'unknown'
  const severity = ['critical', 'high', 'medium', 'low'].includes(level) ? level as Severity : 'unknown'
  const raw = doc.raw_payload
  const cve = [doc.external_id, raw?.vulnerability_id || '', ...(doc.source_aliases || raw?.aliases || [])].find(x => /^CVE-\d{4}-\d+$/i.test(x))
  const excerpt = doc.content || doc.excerpt || ''
  return { id: doc.id, title: doc.title, summary: excerpt.slice(0, 240), severity,
    publishedAt: doc.published_at?.slice(0, 10) || '', source: sources.find(x => x.id === doc.source_id)?.name || '来源待确认',
    cve, products: doc.source_products || raw?.products || [], evidence: [{ title: doc.title, url: doc.source_url, excerpt }],
    tags: [doc.document_type, doc.is_demo ? '虚构样本' : '真实来源', doc.content_completeness],
    contentHash: doc.content_hash, content: doc.content, isDemo: doc.is_demo,
    documentType: doc.document_type, completeness: doc.content_completeness }
}
