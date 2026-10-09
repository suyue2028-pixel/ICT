// The menu and sidebar gestures use the same page order.
export const workspacePages = [
  { to: '/', label: '首页' },
  { to: '/intelligence', label: '情报中心' },
  { to: '/graph', label: '情报分类' },
  { to: '/qa', label: '可追溯问答' },
]

export function isWorkspacePage(path: string, page: string) {
  return path === page || (page !== '/' && path.startsWith(`${page}/`))
}
