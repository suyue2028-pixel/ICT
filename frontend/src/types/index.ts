export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'unknown'
export interface Evidence {
  title: string
  url?: string
  excerpt?: string
}
export interface IntelligenceItem {
  id: string
  title: string
  summary: string
  severity: Severity
  publishedAt: string
  source: string
  cve?: string
  cvss?: number
  cwe?: string
  products: string[]
  recommendation?: string
  evidence: Evidence[]
  tags: string[]
  contentHash?: string
  content?: string
  isDemo?: boolean
  documentType?: string
  completeness?: string
}
export interface GraphNode {
  id: string
  name: string
  category: '情报' | '漏洞' | '产品' | '弱点' | '来源'
  description: string
  intelligenceIds: string[]
}
export interface GraphLink {
  source: string
  target: string
  relation: string
}
export type TaskStage = '采集' | '富化' | '审核' | '问答'
export type TaskStatus = '已完成' | '待处理' | '失败' | '未接入'
export interface TaskRecord {
  id: string
  stage: TaskStage
  name: string
  status: TaskStatus
  time?: string
  detail: string
  error?: string
}
export interface Citation {
  contentHash?: string
  intelligenceId: string
  label: string
}
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  time: string
  citations?: Citation[]
  simulated?: boolean
  generationMode?: string
  warnings?: string[]
  evidence?: { document_id: string; content_hash: string; content: string; source: string; locator: string }[]
  requestId?: string
}
