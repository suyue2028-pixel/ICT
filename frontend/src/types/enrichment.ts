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
}
