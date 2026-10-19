import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { answerBlocks } from '../src/utils/answerPresentation.ts'
import { normalizeToolTrace, returnedWorkflow, coverageStatusLabel, questionTypeLabel, stopReasonLabel, traceStatusLabel } from '../src/utils/answerWorkflow.ts'
import { generationModeLabel, isUnverifiedMessage, messageGenerationLabel, messageHeading, showSecurityEvidence, toolLabel, warningText, UNVERIFIED_EXPLANATION_LABEL } from '../src/utils/chatPresentation.ts'
import { classifyFailure, shouldInvalidateCredential } from '../src/utils/requestFailure.ts'

const fixtures = JSON.parse(readFileSync(new URL('./fixtures/semantic_responses.json', import.meta.url), 'utf8'))
assert.equal(fixtures.synthetic, true)
function templateRender(path) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  return new Function('Vue', compile(parse(source).descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
}
const contentRender = templateRender('../src/components/chat/AnswerContent.vue')
const conversationRender = templateRender('../src/components/chat/ChatConversation.vue')
async function html(message) {
  const app = Vue.createSSRApp({ render: conversationRender, setup: () => ({ messages: [message], sending: false,
    messageHeading, warningText, coverageStatusLabel, questionTypeLabel, stopReasonLabel, traceStatusLabel,
    revealEvidence: () => {}, isUnverifiedMessage, messageGenerationLabel, showSecurityEvidence, toolLabel }) })
  app.component('AnswerContent', { props: ['content', 'plain'], render: contentRender,
    setup: props => ({ blocks: Vue.computed(() => answerBlocks(props.content, props.plain)) }) })
  app.component('router-link', { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', {
    href: props.to.path + (props.to.query ? `?${new URLSearchParams(props.to.query)}` : ''),
  }, slots.default?.()) })
  return renderToString(app)
}
function message(answer) {
  return { id: answer.id, role: 'assistant', time: '', content: answer.answer,
    responseKind: answer.response_kind, retrievalMode: answer.retrieval_mode, status: answer.status,
    generationMode: generationModeLabel(answer, { 'x-rag-answer-mode': 'live' }), evidence: answer.evidence,
    citations: [], warnings: answer.partial_errors, workflow: returnedWorkflow(answer, answer.evidence.length),
    toolTrace: normalizeToolTrace(answer.tool_trace) }
}

for (const fixture of fixtures.cases.filter(row => row.response_kind === 'conversation')) {
  test(`daily fixture ${fixture.id} displays natural reply without security panels or insufficiency`, async () => {
    const output = await html(message(fixture))
    assert.match(output, /基础对话/)
    assert.ok(output.includes(fixture.answer))
    assert.doesNotMatch(output, /知识库.*不足|资料不足|没有可核验|核对原文证据|引用的来源资料|问题范围与证据覆盖|检索与执行记录/)
    assert.doesNotMatch(output, /<blockquote|answer-versions/)
  })
}
test('unverified general explanation is prominently labelled and retains real failed search records', async () => {
  const fixture = { ...fixtures.cases.find(row => row.id === 'general_explanation'), question_type: 'general_explanation',
    tool_trace: [{ tool: 'web_search', status: 'failed', duration_ms: 8, error_code: 'search_not_configured', document_ids: [] }] }
  const output = await html(message(fixture))
  assert.ok(output.includes(UNVERIFIED_EXPLANATION_LABEL))
  assert.match(output, /联网检索/)
  assert.match(output, /调用失败/)
  assert.match(output, /搜索服务尚未配置/)
  assert.doesNotMatch(output, /资料回答|没有可核验的引用证据|核对原文证据|引用的来源资料|证据支持/)
})
test('plain conversation and unverified text cannot turn numbers or source-looking text into citations', async () => {
  for (const kind of ['conversation', 'llm_unverified']) {
    const sourceLooking = '来源《这是字符串》的原文摘录：2+2=4 [1]'
    const output = await html({ id: kind, role: 'assistant', time: '', content: sourceLooking,
      responseKind: kind === 'conversation' ? 'conversation' : 'knowledge', retrievalMode: kind,
      evidence: [], citations: [], warnings: [] })
    assert.ok(output.includes('2+2=4 [1]'))
    assert.doesNotMatch(output, /<blockquote|class="answer-source"|引用的来源资料|核对原文证据/)
    assert.ok(answerBlocks(sourceLooking, true).every(row => row.kind === 'text'))
  }
})
test('all typed search errors differ from model, user authorization and evidence shortage', () => {
  const codes = ['search_not_configured', 'search_auth_failed', 'search_rate_limited', 'search_timeout',
    'search_source_unavailable', 'search_invalid_response', 'search_unsafe_url', 'search_response_too_large', 'search_busy']
  for (const code of codes) {
    const result = classifyFailure({ status: 503, data: { error: { code } } })
    assert.equal(result.kind, 'search')
    assert.doesNotMatch(result.message, /用户会话.*过期|重新登录|证据不足/)
    assert.equal(shouldInvalidateCredential('Bearer user-fixture', 'user-fixture', { status: 401, data: { detail: code } }), false)
    assert.match(warningText([`web_search:${code}`]), /联网|搜索/)
  }
  assert.equal(classifyFailure({ status: 503, data: { detail: 'local_retrieval_failed' } }).kind, 'retrieval')
  assert.equal(classifyFailure({ status: 503, data: { detail: 'model_classification_failed' } }).kind, 'model')
  assert.equal(classifyFailure({ status: 401, data: { detail: 'invalid_user_token' } }).kind, 'user_auth')
})
test('failed retrieval is not labelled as data insufficiency while genuine no-result remains distinct', () => {
  const base = { status: 'insufficient_evidence', response_kind: 'knowledge', retrieval_mode: 'hybrid', evidence: [] }
  assert.match(generationModeLabel({ ...base, partial_errors: ['search_timeout'] }, {}), /联网检索未完成/)
  assert.match(generationModeLabel({ ...base, partial_errors: ['local_retrieval_failed'] }, {}), /本地检索服务异常/)
  assert.match(generationModeLabel({ ...base, partial_errors: ['model_classification_failed'] }, {}), /模型服务异常/)
  assert.match(generationModeLabel({ ...base, partial_errors: [] }, {}), /证据不足/)
  assert.equal(messageHeading({ id: 'failure', role: 'assistant', content: '', time: '', warnings: ['search_timeout'], evidence: [], status: 'insufficient_evidence' }), '联网检索未完成')
})
test('web search appears only for actual returned records and its empty result is not success', async () => {
  const base = { id: 'actual-trace-fixture', role: 'assistant', time: '', content: '资料尚未取得。',
    responseKind: 'knowledge', retrievalMode: 'hybrid', citations: [], evidence: [], warnings: [] }
  assert.doesNotMatch(await html(base), /联网检索/)
  const output = await html({ ...base, toolTrace: normalizeToolTrace([
    { tool: 'web_search', status: 'empty', duration_ms: 3, document_ids: [] },
    { tool: 'web_search', status: 'running', duration_ms: 0 },
  ]) })
  assert.match(output, /联网检索/)
  assert.match(output, /没有结果/)
  assert.doesNotMatch(output, /已完成|实时搜索成功|独立抓取|全文/)
})
test('completed web search displays only returned execution facts and does not manufacture source links', async () => {
  const output = await html({ id: 'completed-trace-only', role: 'assistant', time: '', content: '执行记录样例。',
    responseKind: 'knowledge', retrievalMode: 'hybrid', citations: [], evidence: [], warnings: [],
    toolTrace: normalizeToolTrace([{ tool: 'web_search', status: 'completed', duration_ms: 12,
      input_summary: 'synthetic frontend trace fixture', document_ids: ['synthetic-returned-document'] }]) })
  assert.match(output, /联网检索/)
  assert.match(output, /已完成/)
  assert.match(output, /12 ms/)
  assert.match(output, /synthetic-returned-document/)
  assert.doesNotMatch(output, /引用的来源资料|核对原文证据|独立抓取|全文/)
})
