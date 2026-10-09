export const sourceCategories = [
  { key: 'vulnerability', name: '漏洞数据库' },
  { key: 'vendor', name: '厂商安全公告' },
  { key: 'community', name: '安全社区' },
  { key: 'blog', name: '安全技术博客' },
  { key: 'academic', name: '学术论文' },
  { key: 'standard', name: '技术标准与规范' },
  { key: 'policy', name: '政策法规' },
] as const
export const sourceDisplayNames: Record<string, string> = {
  osv: 'OSV', nvd: 'NVD', cisco: 'Cisco PSIRT', nvidia: 'NVIDIA', sans: 'SANS ISC',
  huggingface: 'Hugging Face', trailofbits: 'Trail of Bits', arxiv: 'arXiv',
  crossref: 'Crossref', nist: 'NIST AI RMF', govuk: 'GOV.UK', china_policy: '中国政府网', offline: '离线演示样本',
}
export const enrichmentDimensions = [
  { key: 'affected', name: '受影响产品与版本', description: '软件包、产品、受影响版本范围' },
  { key: 'cwe', name: 'CWE', description: '漏洞弱点类型' },
  { key: 'cvss', name: 'CVSS', description: '严重程度评分、评分版本和向量' },
  { key: 'remediation', name: '修复信息', description: '修复版本、建议、补丁链接' },
  { key: 'analysis', name: '技术分析', description: '来源提供的分析资料及链接' },
  { key: 'poc', name: '公开 POC 元数据', description: '公开链接和相关信息' },
  { key: 'papers', name: '关联论文', description: '与当前情报相关的论文及引用证据' },
  { key: 'exploitation', name: '公开利用状态', description: '来源报告的公开利用状态及其证据' },
] as const
export type EnrichmentDimensionKey = typeof enrichmentDimensions[number]['key']
