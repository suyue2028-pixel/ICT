import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyFailure, isUserAuthorizationFailure, isVerifiedUserIdentity, knownSessionExpired, publicErrorCode, safeReturnPath, shouldInvalidateCredential } from '../src/utils/requestFailure.ts'
import { normalizeToolTrace, normalizeWorkflow, returnedWorkflow } from '../src/utils/answerWorkflow.ts'
import { readFailedQuestion, readQuestionDraft, saveFailedQuestion, saveQuestionDraft } from '../src/utils/queryDraft.ts'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { isUnverifiedMessage, messageGenerationLabel, showSecurityEvidence, toolLabel } from '../src/utils/chatPresentation.ts'

test('only explicit user authorization errors invalidate a user credential', () => {
  for (const code of ['user_token_required', 'invalid_user_token', 'user_session_expired', 'user_session_revoked']) {
    assert.equal(isUserAuthorizationFailure({ status: 401, data: { detail: code } }), true)
    assert.equal(classifyFailure({ status: 401, data: { error: { code } } }).kind, 'user_auth')
  }
  for (const code of ['platform_model_auth_failed', 'deepseek_auth_failed', 'invalid_api_key', 'unknown']) {
    assert.equal(isUserAuthorizationFailure({ status: 401, data: { detail: code } }), false)
  }
  assert.equal(isUserAuthorizationFailure({ status: 502, data: { detail: 'invalid_user_token' } }), false)
})
test('upstream model authentication, timeout and rate limit stay separate from sign-in', () => {
  for (const [status, code] of [[502, 'platform_model_auth_failed'], [504, 'platform_model_timeout'], [503, 'platform_model_rate_limited']]) {
    const result = classifyFailure({ status, data: { error: { code } } })
    assert.equal(result.kind, 'model')
    assert.doesNotMatch(result.message, /重新登录|重新授权/)
  }
  assert.equal(classifyFailure({ status: 401, data: { detail: 'upstream_unauthorized' } }).kind, 'service')
  assert.equal(classifyFailure({ code: 'ECONNABORTED' }).kind, 'timeout')
  assert.equal(classifyFailure({ status: 503, data: { detail: 'retrieval_failed' } }).kind, 'retrieval')
})
test('old request failures cannot clear a newly authorized credential', () => {
  const failure = { status: 401, data: { detail: 'invalid_user_token' } }
  assert.equal(shouldInvalidateCredential('Bearer old-user', 'new-user', failure), false)
  assert.equal(shouldInvalidateCredential('Bearer current-user', 'current-user', failure), true)
  assert.equal(shouldInvalidateCredential('', '', failure), false)
  assert.equal(knownSessionExpired('2026-10-11T00:00:00Z', Date.parse('2026-10-11T01:00:00Z')), true)
  assert.equal(knownSessionExpired(null), false)
  assert.equal(knownSessionExpired('invalid-date'), true)
})
test('a proxy HTML response or unverified identity cannot authorize the user', () => {
  assert.equal(isVerifiedUserIdentity({ authenticated: true, owner_id: 'test-owner' }), true)
  for (const response of ['<html>index</html>', { owner_id: 'test-owner' }, { authenticated: false, owner_id: 'test-owner' }, { authenticated: true, owner_id: '' }]) assert.equal(isVerifiedUserIdentity(response), false)
})
test('redirects remain internal and error bodies are never displayed verbatim', () => {
  assert.equal(safeReturnPath('/qa'), '/qa')
  for (const path of ['https://outside.example', '//outside.example', '/\\outside.example', '/login', null]) assert.equal(safeReturnPath(path), '/qa')
  assert.equal(publicErrorCode({ detail: { code: 'platform_model_not_configured' } }), 'platform_model_not_configured')
  assert.doesNotMatch(classifyFailure({ status: 502, data: { detail: 'arbitrary-private-body' } }).message, /arbitrary-private-body/)
})
test('workflow is optional, evidence references are bounded, and missing sources are not credited', () => {
  assert.equal(normalizeWorkflow(undefined, 0), undefined)
  const result = normalizeWorkflow({ question_type: 'security_topic', requirement_coverage: [
    { requirement_id: 'a', label: '已支持', status: 'supported', evidence_ids: ['01', '1', '2', '99'], reason: '原始证据' },
    { requirement_id: 'b', label: '无证据', status: 'supported', evidence_ids: ['99'], reason: '不可接受' },
    { requirement_id: 'c', label: '冲突', status: 'conflicting', evidence_ids: ['2'], reason: '来源分歧' },
  ], retrieval_summary: { rounds: 2, stop_reason: 'no_new_evidence', initial_tools: ['hybrid_search'], supplemental_tools: ['query_nvd'] }, limitations: ['资料不完整'] }, 2)
  assert.deepEqual(result.requirement_coverage[0].evidence_ids, ['1', '2'])
  assert.equal(result.requirement_coverage[1].status, 'missing')
  assert.equal(result.requirement_coverage[2].status, 'conflicting')
  assert.equal(result.retrieval_summary.rounds, 2)
})
test('only completed/failed/empty returned tool records are displayed', () => {
  assert.deepEqual(normalizeToolTrace(undefined), [])
  const traces = normalizeToolTrace([
    { tool: 'query_cve', status: 'completed', duration_ms: 12, document_ids: ['doc-1'] },
    { tool: 'hypothetical-live-step', status: 'running', duration_ms: 0 },
    { tool: 'invalid', status: 'completed', duration_ms: -1 },
    { tool: 'query_nvd', status: 'empty', duration_ms: 7 },
  ])
  assert.equal(traces.length, 2)
  assert.equal(traces[1].status, 'empty')
})
test('flat V1 workflow fields take precedence while old and nested responses remain compatible', () => {
  const workflow = { question_type: 'cve', requirement_coverage: [], retrieval_summary: { rounds: 2, stop_reason: 'no_new_evidence' }, limitations: ['字段缺失'] }
  assert.equal(returnedWorkflow({ answer: 'answer', ...workflow }, 1).question_type, 'cve')
  assert.equal(returnedWorkflow({ workflow }, 1).retrieval_summary.rounds, 2)
  assert.equal(returnedWorkflow({ answer: 'older answer', evidence: [] }, 0), undefined)
  assert.equal(returnedWorkflow({ response_kind: 'conversation', question_type: null, requirement_coverage: [], retrieval_summary: null, limitations: [] }, 0), undefined)
  assert.equal(returnedWorkflow({ ...workflow, workflow: { question_type: 'wrong' } }, 1).question_type, 'cve')
})
test('multi-CVE requirement rows retain the V1 bound of forty independent requirements', () => {
  const rows = Array.from({ length: 40 }, (_, index) => ({ requirement_id: `cvss_${index}`, label: `需求 ${index}`, status: 'missing', evidence_ids: [], reason: '缺少资料' }))
  assert.equal(returnedWorkflow({ question_type: 'cve', requirement_coverage: rows }, 0).requirement_coverage.length, 40)
})
test('draft and failed question survive authorization navigation, with real/demo scopes separated', () => {
  const values = new Map()
  globalThis.sessionStorage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  saveQuestionDraft(false, '真实资料问题')
  saveFailedQuestion(false, '未完成的问题')
  saveQuestionDraft(true, '虚构样本问题')
  assert.equal(readQuestionDraft(false), '真实资料问题')
  assert.equal(readFailedQuestion(false), '未完成的问题')
  assert.equal(readQuestionDraft(true), '虚构样本问题')
  assert.equal(readFailedQuestion(true), '')
  saveFailedQuestion(false, '')
  assert.equal(readFailedQuestion(false), '')
  assert.equal([...values.keys()].some(key => /token|api-key/i.test(key)), false)
})
test('rendered coverage prefers workflow and source text shows the title while retaining its route and version', async () => {
  const source = readFileSync(new URL('../src/components/chat/ChatConversation.vue', import.meta.url), 'utf8')
  const template = parse(source).descriptor.template.content
  const render = new Function('Vue', compile(template, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
  async function html(message) {
    const app = Vue.createSSRApp({ render, setup: () => ({ messages: [message], sending: false,
      messageHeading: () => '资料回答', warningText: () => '', coverageStatusLabel: { supported: '证据支持' },
      questionTypeLabel: () => '安全知识问题', stopReasonLabel: () => '已结束', traceStatusLabel: {}, revealEvidence: () => {},
      isUnverifiedMessage, messageGenerationLabel, showSecurityEvidence, toolLabel }) })
    app.component('AnswerContent', { props: ['content'], setup: props => () => Vue.h('p', props.content) })
    app.component('router-link', { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', {
      href: props.to.path + (props.to.query ? `?${new URLSearchParams(props.to.query)}` : ''),
    }, slots.default?.()) })
    return renderToString(app)
  }
  const common = { id: 'render-test', role: 'assistant', content: '正文', time: '',
    coverage: { answered_topics: ['旧支持标签'], missing_topics: ['旧缺失标签'] },
    citations: [{ intelligenceId: 'source-uuid', contentHash: 'source-version', label: '真实来源标题' }] }
  const modern = await html({ ...common, workflow: { question_type: 'security_knowledge',
    requirement_coverage: [{ requirement_id: 'permissions', label: '新权限需求', status: 'supported', evidence_ids: [], reason: '' }],
    retrieval_summary: { rounds: 1, stop_reason: 'requirements_supported', initial_tools: [], supplemental_tools: [] }, limitations: [] } })
  assert.match(modern, /新权限需求/)
  assert.doesNotMatch(modern, /旧支持标签|旧缺失标签/)
  assert.match(modern, /href="\/intelligence\/source-uuid\?content_hash=source-version"[^>]*>真实来源标题 ↗<\/a>/)
  assert.doesNotMatch(modern, />source-uuid · /)
  const legacy = await html(common)
  assert.match(legacy, /旧支持标签/)
  assert.match(legacy, /旧缺失标签/)
})
