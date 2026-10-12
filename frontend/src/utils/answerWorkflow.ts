import type { AnswerWorkflow, AnswerToolTrace, RequirementCoverage } from '../types/index'
const record = (value: unknown): Record<string, unknown> | undefined => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
const text = (value: unknown, max = 500): string => typeof value === 'string' ? value.trim().slice(0, max) : ''
const strings = (value: unknown, max = 12): string[] => Array.isArray(value) ? [...new Set(value.map(row => text(row, 120)).filter(Boolean))].slice(0, max) : []
export function normalizeWorkflow(value: unknown, evidenceCount: number): AnswerWorkflow | undefined {
  const row = record(value)
  if (!row) return undefined
  const summary = record(row.retrieval_summary)
  const requirements: RequirementCoverage[] = Array.isArray(row.requirement_coverage) ? row.requirement_coverage.slice(0, 40).flatMap(value => {
    const requirement = record(value)
    if (!requirement) return []
    const label = text(requirement.label, 120), id = text(requirement.requirement_id, 120)
    if (!label || !id || !['supported', 'partial', 'conflicting', 'missing'].includes(String(requirement.status))) return []
    const ids = [...new Set(strings(requirement.evidence_ids, 50).filter(id => /^\d+$/.test(id) && Number(id) > 0 && Number(id) <= evidenceCount).map(id => String(Number(id))))]
    const rawStatus = requirement.status as RequirementCoverage['status']
    // Display cannot credit a source that is absent from the actual response.
    const status = rawStatus !== 'missing' && !ids.length ? 'missing' : rawStatus
    return [{ requirement_id: id, label, status, evidence_ids: ids, reason: status !== rawStatus ? '返回结果未包含可核验的对应证据。' : text(requirement.reason) }]
  }) : []
  return { question_type: text(row.question_type, 80), requirement_coverage: requirements,
    retrieval_summary: { rounds: typeof summary?.rounds === 'number' && Number.isInteger(summary.rounds) ? Math.max(0, Math.min(10, summary.rounds)) : 0,
      stop_reason: text(summary?.stop_reason, 160), initial_tools: strings(summary?.initial_tools, 30), supplemental_tools: strings(summary?.supplemental_tools, 30) },
    limitations: strings(row.limitations, 20) }
}
/** The V1 backend contract is flat. Accept an older nested extension as fallback. */
export function returnedWorkflow(value: unknown, evidenceCount: number): AnswerWorkflow | undefined {
  const row = record(value)
  if (!row) return undefined
  if (['question_type', 'requirement_coverage', 'retrieval_summary', 'limitations'].some(key => key in row)) {
    const hasRequirements = Array.isArray(row.requirement_coverage) && row.requirement_coverage.length > 0
    if (!text(row.question_type) && !hasRequirements && !record(row.retrieval_summary) && !strings(row.limitations).length) return undefined
    return normalizeWorkflow(row, evidenceCount)
  }
  return normalizeWorkflow(row.workflow, evidenceCount)
}
export function normalizeToolTrace(value: unknown): AnswerToolTrace[] {
  return Array.isArray(value) ? value.slice(0, 30).flatMap(value => {
    const row = record(value)
    if (!row || !text(row.tool, 80) || !['completed', 'empty', 'failed'].includes(String(row.status))
      || typeof row.duration_ms !== 'number' || !Number.isFinite(row.duration_ms) || row.duration_ms < 0) return []
    return [{ tool: text(row.tool, 80), status: row.status as AnswerToolTrace['status'], duration_ms: Math.round(row.duration_ms),
      error_code: text(row.error_code, 100), input_summary: text(row.input_summary, 500), document_ids: strings(row.document_ids, 30) }]
  }) : []
}
export const coverageStatusLabel = { supported: '证据支持', partial: '部分支持', conflicting: '来源冲突', missing: '资料缺失' } as const
export const traceStatusLabel = { completed: '已完成', empty: '没有结果', failed: '调用失败' } as const
export function stopReasonLabel(reason: string): string {
  const labels: Record<string, string> = { sufficient_evidence: '已有足够证据', no_missing_requirements: '已覆盖所需范围',
    no_new_evidence: '没有新增证据', budget_exhausted: '已达到调用预算', max_rounds: '已达到检索轮次限制',
    requirements_supported: '已取得需求对应资料，结论另经核验', clarification_required: '需要明确查询对象或输入条件',
    tool_budget_reserved: '已为完成回答预留工具预算', deadline_reserved: '已接近请求时间上限',
    model_budget_reserved: '已为答案生成与核验预留模型预算', model_failed: '模型生成或核验失败',
    available_tools_exhausted: '可用工具已执行，未找到更多资料', not_started: '尚未开始资料检索',
    general_explanation: '仅提供模型一般解释，未完成来源核验', semantic_conversation: '日常对话不执行资料检索',
    insufficient_evidence: '当前资料不足', tool_unavailable: '所需工具尚不可用', conversation: '基础对话不执行资料检索' }
  return labels[reason] || (reason ? '工作流已结束，具体原因以服务端记录为准' : '未提供停止原因')
}
export function questionTypeLabel(kind: string): string {
  const labels: Record<string, string> = { cve: 'CVE 漏洞查询', security_knowledge: '安全知识问题', comparison: '资料对比', package: '依赖包漏洞查询', clarification: '需要澄清的问题', conversation: '基础对话', general_explanation: '模型一般解释' }
  return labels[kind] || '其他问题'
}
