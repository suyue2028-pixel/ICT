import type { PaperEnrichment, PaperQuote } from '@/types'

import type { EnrichmentResult } from '@/types/enrichment'
export type { EnrichmentResult } from '@/types/enrichment'

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

export function publicUrl(value: unknown): string | undefined {
  const candidate = text(value)
  if (!candidate) return undefined
  try {
    const url = new URL(candidate)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}

function citationUrl(value: unknown): string | undefined {
  const href = publicUrl(value)
  if (!href) return undefined
  const url = new URL(href)
  url.hash = ''
  return url.href
}

function paperQuote(value: unknown): PaperQuote | undefined {
  const row = record(value)
  const documentId = text(row.document_id), contentHash = text(row.content_hash)
  const sourceUrl = publicUrl(row.source_url), locator = text(row.locator), excerpt = text(row.excerpt)
  if (!documentId || !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(documentId)
      || !contentHash || !/^[a-f0-9]{64}$/.test(contentHash)
      || !sourceUrl || !locator || !excerpt || excerpt.length > 2000) return undefined
  return { documentId, contentHash, sourceUrl, locator, excerpt }
}

// GET /documents/{id}/enrichment returns the saved EnrichmentWrite payload
// inside external.papers[].value. Never use the paper body as a quotation.
export function paperEnrichments(results: EnrichmentResult[] = []): PaperEnrichment[] {
  return results.flatMap(result => {
    if (!['known', 'pending_review'].includes(result.state)) return []
    const payload = record(result.value)
    const papers = Array.isArray(payload.value) ? payload.value : []
    const citations = (Array.isArray(payload.evidence) ? payload.evidence : [])
      .map(paperQuote).filter((quote): quote is PaperQuote => !!quote)

    return papers.map(entry => {
      const metadata = record(entry)
      const url = publicUrl(metadata.url)
      // Evidence can cite the original intelligence as well as a paper.
      // Only associate quotations with the paper whose URL they actually cite.
      const quotes = citations.filter(quote => !!url && citationUrl(quote.sourceUrl) === citationUrl(url))
      const uniqueQuotes = [...new Map(quotes.map(quote => [
        `${quote.documentId}:${quote.contentHash}:${quote.locator}:${quote.excerpt}`, quote,
      ])).values()]
      return {
        title: text(metadata.title), url, enrichmentType: '论文关联' as const,
        method: text(payload.extraction_method), reviewState: text(payload.review_state),
        state: result.state, provider: result.provider, quotes: uniqueQuotes,
      }
    })
  })
}
