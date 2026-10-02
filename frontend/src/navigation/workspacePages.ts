import { ChatDotRound, DataAnalysis, Document, List, Share } from '@element-plus/icons-vue'

// The menu and sidebar gestures use the same page order.
export const workspacePages = [
  { to: '/', label: '总览', icon: DataAnalysis },
  { to: '/intelligence', label: '情报中心', icon: Document },
  { to: '/graph', label: '知识图谱', icon: Share },
  { to: '/qa', label: '可追溯问答', icon: ChatDotRound },
  { to: '/tasks', label: '运行状态', icon: List },
]

export function isWorkspacePage(path: string, page: string) {
  return path === page || (page !== '/' && path.startsWith(`${page}/`))
}
