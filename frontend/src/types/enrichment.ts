export interface EnrichmentResult {
  state: string
  value?: unknown
  provider?: string
  evidence_id?: string | null
}
export interface EnrichmentSnapshot {
  document_id?: string
  content_hash: string
  source: Record<string, EnrichmentResult>
  external?: Record<string, EnrichmentResult[]>
  summary?: Record<string, { state: string; checked_at?: string; reason_code?: string; applicability?: string; scope?: string }>
}
