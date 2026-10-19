export interface SourceExcerpt {
  text: string
  truncatedBefore: boolean
  truncatedAfter: boolean
}

export const documentTypeNames: Record<string, string> = {
  vulnerability: '漏洞情报', advisory: '安全通告', article: '安全文章',
  discussion: '社区讨论', paper: '研究论文', standard: '标准规范',
  policy: '政策文件', regulation: '法律法规',
}

const sentenceSegmenter = new Intl.Segmenter(['zh', 'en'], { granularity: 'sentence' })
const securityPatterns = [
  /prompt[\s_-]*injection|提示(?:词)?注入|sql\s+injection|SQL\s*注入|反序列化|deserializ\w*/iu,
  /remote code execution|arbitrary code|SSRF|authentication bypass|privilege escalation|denial of service|data exfiltration|远程代码执行|访问控制|权限提升|信息泄露/iu,
  /\b(?:attacker|malicious|untrusted|vulnerab\w*|security|impact|risk|exploit\w*)\b|攻击|漏洞|风险|利用/iu,
]
const maximumExcerpts = 3, maximumLength = 240

// Select bounded, literal source spans. This is a page excerpt, not a generated
// summary or a claim that the backend has extracted/verified these sentences.
export function sourceExcerpts(content: string, keywords: string[] = []): SourceExcerpt[] {
  const terms = [...new Set(keywords.map(term => term.trim().toLowerCase()).filter(Boolean))]
  const candidates: { text: string; index: number; score: number; focus: number }[] = []
  for (const line of content.matchAll(/[^\r\n]+/g)) {
    if (/^\s*#{1,6}\s+[^.!?。！？]{1,100}\s*$/u.test(line[0])
        || /^\s*(?:[-*_]{3,}|```.*|https?:\/\/\S+)\s*$/u.test(line[0])) continue
    for (const segment of sentenceSegmenter.segment(line[0])) {
      const text = segment.segment.trim()
      if (!text) continue
      const lower = text.toLowerCase()
      let score = 0, focus = -1
      for (const term of terms) {
        const position = lower.indexOf(term)
        if (position < 0) continue
        score += /^cve-|^cwe-/i.test(term) ? 12 : 3
        if (focus === -1) focus = position
      }
      securityPatterns.forEach((pattern, index) => {
        const match = pattern.exec(text)
        if (!match) return
        score += (securityPatterns.length - index) * 4
        if (focus === -1) focus = match.index
      })
      candidates.push({ text, index: line.index + segment.index, score, focus: Math.max(0, focus) })
    }
  }
  const unique = [...new Map(candidates.map(candidate => [candidate.text, candidate])).values()]
  const ranked = unique.sort((a, b) => b.score - a.score || a.index - b.index)
  // When no keyword matches, present one opening excerpt rather than pretending
  // to have identified an intelligence-specific key sentence.
  const relevant = ranked.filter(candidate => candidate.score > 0)
  const selected = (relevant.length ? relevant : ranked.slice(0, 1))
    .slice(0, maximumExcerpts).sort((a, b) => a.index - b.index)

  return selected.map(candidate => {
    const text = candidate.text
    let start = Math.max(0, Math.min(candidate.focus - 60, text.length - maximumLength))
    if (start > 0) {
      const boundary = text.indexOf(' ', start)
      if (boundary >= start && boundary < start + 24 && boundary < candidate.focus) start = boundary + 1
    }
    let end = Math.min(text.length, start + maximumLength)
    if (end < text.length) {
      const boundary = text.lastIndexOf(' ', end)
      if (boundary > start + maximumLength - 40 && boundary > candidate.focus) end = boundary
    }
    return { text: text.slice(start, end).trim(), truncatedBefore: start > 0, truncatedAfter: end < text.length }
  })
}
