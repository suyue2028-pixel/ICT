import type { Citation, IntelligenceItem } from '@/types'

// Local, deterministic matching for the demo. These topics are examples, not RAG retrieval.
const topics = [
  { question: /提示注入/, record: /提示注入/ },
  { question: /检索|\brag\b/i, record: /检索|\brag\b/i },
  { question: /审计/, record: /审计/ },
  { question: /智能体|工具/, record: /智能体|工具/ },
  { question: /访问控制/, record: /访问控制/ },
  { question: /权限|鉴权/, record: /权限|鉴权/ },
  { question: /来源|引用|溯源/, record: /来源|引用|溯源/ },
]

export function createDemoAnswer(
  question: string,
  items: IntelligenceItem[],
): { content: string; citations: Citation[] } {
  const query = question.trim().toLowerCase()
  const matchedTopics = topics.filter((topic) => topic.question.test(query))
  const latinTerms = query.match(/[a-z0-9-]{2,}/g) || []

  const scored = items
    .map((item) => {
      const corpus =
        `${item.title} ${item.summary} ${item.tags.join(' ')} ${item.products.join(' ')}`.toLowerCase()
      const topicScore = matchedTopics.filter((topic) => topic.record.test(corpus)).length
      const termScore = latinTerms.filter((term) => corpus.includes(term)).length
      return { item, score: topicScore * 10 + termScore }
    })
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 2)

  if (!scored.length) {
    return {
      content:
        '本地演示数据中没有找到与这个问题直接相关的记录。请尝试询问“智能体工具审计”“访问控制”或“提示注入”。这不是 RAG 检索结果。',
      citations: [],
    }
  }

  const citations = scored.map(({ item }) => ({ intelligenceId: item.id, label: item.title }))
  return {
    content: `根据本地演示记录，${scored.map(({ item }) => `${item.title}：${item.summary} 建议：${item.recommendation || '暂无数据'}`).join('\n\n')}\n\n以上为关键词匹配生成的模拟回答，未调用 RAG、知识图谱推理或真实智能体。`,
    citations,
  }
}
