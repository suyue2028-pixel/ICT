import type { TaskRecord } from '@/types'
export function taskSummary(detail: string) {
  try {
    const result = JSON.parse(detail) as Record<string, unknown>
    if (!result || typeof result !== 'object') return detail
    const labels: Record<string, string> = { received: '接收', created: '新增', updated: '更新',
      skipped: '跳过', ai_relevant: 'AI 相关', relevant: '相关', failed: '失败', duplicates: '重复' }
    const parts = Object.entries(labels).flatMap(([key, label]) =>
      typeof result[key] === 'number' ? [`${label} ${result[key]} 条`] : [])
    return parts.length ? parts.join(' · ') : '后端已返回结果，可展开查看原始记录。'
  } catch { return detail || '暂无处理结果' }
}
export function taskDuration(task: TaskRecord) {
  if (!task.createdAt || !task.finishedAt) return ''
  const seconds = (Date.parse(task.finishedAt) - Date.parse(task.createdAt)) / 1000
  if (!Number.isFinite(seconds) || seconds < 0) return ''
  return seconds < 60 ? `${seconds.toFixed(1)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}
export function taskTime(time?: string) {
  if (!time || !Number.isFinite(Date.parse(time))) return time || '暂无数据'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(time))
}
