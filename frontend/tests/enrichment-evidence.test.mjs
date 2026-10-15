import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'

// Load the actual pure presentation module with its one runtime alias resolved.
const path = new URL('../src/utils/enrichmentPresentation.ts', import.meta.url)
const source = stripTypeScriptTypes(readFileSync(path, 'utf8')).replace(
  "'@/api/paperEnrichment'", JSON.stringify(new URL('../src/api/paperEnrichment.ts', import.meta.url).href))
const { dimensionState, dimensionBlocks } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const id = '00000000-0000-4000-8000-000000000001', hash = 'a'.repeat(64)
const item = { id, contentHash: hash, products: [] }
const quote = { document_id: id, content_hash: hash, locator: 'content:chars:0:7', excerpt: 'fixture', source_url: 'https://example.invalid/fixture' }
const snapshot = { document_id: id, content_hash: hash, source: {}, summary: {} }

test('checked absence and inapplicability remain distinct from unprocessed work', () => {
  for (const state of ['not_applicable', 'not_found', 'queued', 'processing', 'failed']) {
    assert.equal(dimensionState(item, { ...snapshot, summary: { affected: { state } } }, 'affected'), state)
  }
})
test('formula-derived CVSS score remains explicitly separate from an official score', () => {
  const data = { ...snapshot, external: { cvss: [{ state: 'known', provider: 'backend-evidence-v1', value: {
    value: [{ version: '3.1', score: 9.8, vector: 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H',
      origin: 'system_estimate', derivation: 'cvss_v3_base_formula' }], evidence: [quote] } }] } }
  const blocks = dimensionBlocks(item, data, 'cvss')
  assert.equal(blocks[0].origin, '后端证据富化')
  assert.ok(blocks[0].facts.some(row => row.includes('非来源发布的官方分数')))
  assert.ok(blocks[0].facts.some(row => row.includes('公式复算')))
})
test('a version mismatch cannot display newer enrichment as historical evidence', () => {
  const data = { ...snapshot, content_hash: 'b'.repeat(64), summary: { affected: { state: 'known' } } }
  assert.equal(dimensionState(item, data, 'affected'), 'historical')
  assert.deepEqual(dimensionBlocks(item, data, 'affected'), [])
})
test('topic-only paper relations remain pending review and retain their version quotation', () => {
  const data = { ...snapshot, external: { papers: [{ state: 'pending_review', provider: 'backend-evidence-v1', value: {
    value: [{ url: 'https://example.invalid/paper', title: 'Fictional paper' }], evidence: [quote], review_state: 'pending_review' } }] } }
  assert.equal(dimensionState(item, data, 'papers'), 'pending_review')
  assert.equal(dimensionBlocks(item, data, 'papers')[0].quotes[0].contentHash, hash)
})
test('public exploit disclosure is never labelled as confirmed active exploitation', () => {
  const data = { ...snapshot, external: { exploitation: [{ state: 'known', provider: 'backend-evidence-v1', value: {
    value: { status: 'public_exploit_disclosed_not_confirmed_active' }, evidence: [quote] } }] } }
  assert.ok(dimensionBlocks(item, data, 'exploitation')[0].facts.some(row => row.includes('尚未确认实际利用')))
})

test('OSV Git fixed events are labelled as commits rather than released versions', () => {
  const commit = 'b'.repeat(40)
  const data = { ...snapshot, source: { affected: { state: 'known', evidence_id: 'fixture-evidence', value: {
    items: [{ package: { name: 'fictional' }, ranges: [{ type: 'GIT', events: [{ fixed: commit }] }] }] } } } }
  const facts = dimensionBlocks(item, data, 'affected')[0].facts
  assert.ok(facts.includes(`fictional · 修复提交：${commit}`))
  assert.ok(!facts.some(row => row.includes(`修复版本：${commit}`)))
})
