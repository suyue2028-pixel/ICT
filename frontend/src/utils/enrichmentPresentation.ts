import type { IntelligenceItem } from '@/types'
import type { EnrichmentSnapshot } from '@/types/enrichment'
import type { EnrichmentDimensionKey } from '@/config/intelligenceCategories'
import { publicUrl } from '@/api/paperEnrichment'

export interface EnrichmentBlock {
  origin: string
  state: string
  evidenceId?: string
  facts: string[]
  links: { title: string; url: string }[]
  quotes: { text: string; documentId: string; contentHash: string; locator: string }[]
}
export const enrichmentStateNames: Record<string, string> = {
  known: '有来源字段', unknown: '来源未提供', unprocessed: '尚未处理', not_found: '未找到',
  failed: '处理失败', pending_review: '待审核', unavailable: '读取失败', historical: '版本不一致',
  not_applicable: '不适用（已检查）',
  queued: '排队处理', processing: '处理中',
}
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const string = (value: unknown): string => typeof value === 'string' ? value : ''
const strings = (value: unknown): string[] => array(value).filter((entry): entry is string => typeof entry === 'string')

function presentValue(key: EnrichmentDimensionKey, value: unknown): Pick<EnrichmentBlock, 'facts' | 'links'> {
  const data = object(value), facts: string[] = [], links: EnrichmentBlock['links'] = []
  if (key === 'affected' || key === 'remediation') {
    for (const item of array(data.items || value).map(object)) {
      const pkg = object(item.package), product = string(pkg.name) || '产品暂无数据'
      if (key === 'affected') facts.push(`产品：${product}${pkg.ecosystem ? ` · ${string(pkg.ecosystem)}` : ''}`)
      for (const range of array(item.ranges).map(object)) {
        for (const event of array(range.events).map(object)) {
          for (const [field, label] of Object.entries({ introduced: '引入标记', fixed: '修复版本', last_affected: '最后受影响版本', limit: '范围上限' })) {
            const version = string(event[field])
            const isCommit = range.type === 'GIT'
            const unitLabel = isCommit ? ({ introduced: '引入提交', fixed: '修复提交', last_affected: '最后受影响提交', limit: '提交范围上限' } as Record<string, string>)[field] || label : label
            if (version && (key === 'affected' || field === 'fixed')) facts.push(`${product} · ${unitLabel}：${version}${field === 'introduced' && version === '0' ? `（来源未指定起始${isCommit ? '提交' : '版本'}）` : ''}`)
          }
        }
      }
      const versions = strings(item.versions)
      if (key === 'affected' && typeof item.cpe_criteria === 'string') facts.push(`来源 CPE：${item.cpe_criteria}`)
      if (key === 'affected') for (const [bound, version] of Object.entries(object(item.version_bounds))) {
        if (typeof version === 'string') facts.push(`${bound}：${version}`)
      }
      if (key === 'affected' && item.version_state === 'unknown') facts.push('受影响版本范围：来源未提供，不能据此判断其他版本安全')
      if (key === 'affected' && versions.length) facts.push(`来源枚举版本：${versions.slice(0, 3).join('、')}${versions.length > 3 ? `等，共 ${versions.length} 个` : ''}`)
    }
    if (key === 'affected') for (const match of array(data.cpe_matches).map(object)) {
      if (typeof match.criteria === 'string') facts.push(`来源 CPE 条件：${match.criteria}`)
      for (const field of ['versionStartIncluding', 'versionStartExcluding', 'versionEndIncluding', 'versionEndExcluding']) {
        if (typeof match[field] === 'string') facts.push(`${field}：${match[field]}`)
      }
    }
  }
  if (key === 'cwe') facts.push(...strings(data.ids || value))
  if (key === 'cvss') for (const vector of array(data.vectors || value).map(object)) {
    facts.push(`评分版本：${string(vector.version) || '暂无数据'}`)
    facts.push(`评分：${typeof vector.score === 'number' ? vector.score : '暂无数据'}`)
    if (typeof vector.vector === 'string') facts.push(`向量：${vector.vector}`)
    if (vector.derivation === 'cvss_v3_base_formula') facts.push('分数按来源向量及 CVSS 3.x 基础公式复算，非来源发布的官方分数')
    if (typeof vector.origin === 'string') facts.push(`评分来源：${vector.derivation === 'cvss_v3_base_formula' ? '来源向量公式复算' : ({ official: '官方', source_reported: '来源报告', system_estimate: '系统估算' } as Record<string, string>)[vector.origin] || vector.origin}`)
  }
  if (key === 'exploitation') {
    if (typeof value === 'boolean') facts.push(`来源报告存在公开利用：${value ? '是' : '否'}`)
    for (const [field, label] of Object.entries({ catalog: '收录目录', status: '来源报告状态', added_at: '收录日期', required_action: '来源要求的措施' })) {
      if (typeof data[field] === 'string') facts.push(`${label}：${data[field] === 'public_exploit_disclosed_not_confirmed_active' ? '来源报告公开了利用代码；尚未确认实际利用' : data[field]}`)
    }
  }
  if (['remediation', 'analysis', 'poc', 'papers'].includes(key)) {
    for (const reference of array(data.references || value).map(object)) {
      const url = publicUrl(reference.url)
      if (!url) continue
      links.push({ url, title: string(reference.title) || string(reference.type) || url })
      if (typeof reference.description === 'string') facts.push(reference.description.slice(0, 240))
      if (Array.isArray(reference.tags)) facts.push(`来源标签：${strings(reference.tags).join('、')}`)
    }
  }
  return { facts: [...new Set(facts)], links }
}

export function dimensionBlocks(item: IntelligenceItem, snapshot: EnrichmentSnapshot | undefined, key: EnrichmentDimensionKey): EnrichmentBlock[] {
  if (!snapshot || snapshot.content_hash !== item.contentHash || snapshot.document_id && snapshot.document_id !== item.id) return []
  const blocks: EnrichmentBlock[] = []
  const source = snapshot.source[key]
  if (source?.state === 'known' && source.evidence_id) {
    const value = presentValue(key, source.value)
    if (value.facts.length || value.links.length) blocks.push({ origin: '来源结构化字段', state: 'known', evidenceId: source.evidence_id, ...value, quotes: [] })
  }
  if (key === 'remediation' && snapshot.source.affected?.state === 'known' && snapshot.source.affected.evidence_id) {
    const value = presentValue('remediation', snapshot.source.affected.value)
    if (value.facts.length) blocks.push({ origin: '来源记录的修复版本', state: 'known', evidenceId: snapshot.source.affected.evidence_id, ...value, quotes: [] })
  }
  for (const result of snapshot.external?.[key] || []) {
    if (!['known', 'pending_review'].includes(result.state)) continue
    const payload = object(result.value)
    const quotes = array(payload.evidence).map(object).filter(citation => /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(string(citation.document_id))
      && /^[a-f0-9]{64}$/.test(string(citation.content_hash)) && string(citation.excerpt) && string(citation.locator))
      .map(citation => ({ text: string(citation.excerpt), documentId: string(citation.document_id), contentHash: string(citation.content_hash), locator: string(citation.locator) }))
    if (!quotes.length) continue
    const value = presentValue(key, payload.value)
    const pending = result.state === 'pending_review' || payload.review_state === 'pending_review' || payload.extraction_method === 'model'
    blocks.push({ origin: result.provider === 'backend-evidence-v1' ? '后端证据富化' : `外部富化${result.provider ? ` · ${result.provider}` : ''}`, state: pending ? 'pending_review' : 'known', ...value, quotes })
  }
  return blocks
}

export function dimensionState(item: IntelligenceItem, snapshot: EnrichmentSnapshot | undefined, key: EnrichmentDimensionKey): string {
  if (!snapshot) return item.structuredStatus === 'historical' ? 'historical' : 'unavailable'
  if (snapshot.content_hash !== item.contentHash || snapshot.document_id && snapshot.document_id !== item.id) return 'historical'
  const blocks = dimensionBlocks(item, snapshot, key)
  if (blocks.some(block => block.state === 'pending_review')) return 'pending_review'
  if (blocks.length) return 'known'
  const checked = snapshot.summary?.[key]
  if (checked && checked.state !== 'unprocessed') return checked.state
  const states = (snapshot.external?.[key] || []).map(result => result.state)
  return states.includes('failed') ? 'failed' : states.includes('not_found') ? 'not_found' : states.includes('unprocessed') ? 'unprocessed' : 'unknown'
}
