export interface AnswerBlock {
  kind: 'text' | 'quote' | 'versions'
  text: string
  title?: string
  reference?: string
  fields?: { label: string; value: string }[]
  incomplete?: boolean
}

// Present only facts that appear in the answer. Original text is always retained.
export function answerBlocks(content: string, plain = false): AnswerBlock[] {
  if (plain) return content.split(/\n+/).filter(line => line.trim()).map(text => ({ kind: 'text', text }))
  return content.split(/\n+/).filter(line => line.trim()).map(line => {
    const quote = line.match(/^来源《(.+?)》的原文摘录：(.*?)(\s*\[\d+\])?$/)
    if (quote) return { kind: 'quote', title: quote[1], text: quote[2] || '', reference: quote[3]?.trim() }
    const field = line.match(/^已记录 (.+?) 的 affected_version：(.*?)(\s*\[\d+\])?$/)
    if (!field) return { kind: 'text', text: line }
    const source = (field[2] || '').replace(/\\"/g, '"')
    const values = (key: string) => [...new Set([...source.matchAll(
      new RegExp(`"${key}"\\s*:\\s*"([^"\\n]+)"`, 'g'))].map(match => match[1]!))].join('、')
    const fields = [
      { label: '软件包', value: values('name') },
      { label: '生态', value: values('ecosystem') },
      { label: '来源记录的修复版本', value: values('fixed') },
      { label: '来源记录的最后受影响版本', value: values('last_affected') },
    ].filter(item => item.value)
    if (!fields.length) return { kind: 'text', text: line }
    let incomplete = false
    try { JSON.parse(source) } catch { incomplete = true }
    return { kind: 'versions', title: field[1], text: line, fields, incomplete,
      reference: field[3]?.trim() }
  })
}
