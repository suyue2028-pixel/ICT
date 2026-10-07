import { readFileSync, statSync } from 'node:fs'
import type { Plugin } from 'vite'

// Development-only bridge. Service credentials stay in this Node process.
export function localRuntimeStatus(): Plugin {
  return {
    name: 'local-runtime-status', apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url?.split('?')[0] !== '/__local/runtime') return next()
        response.setHeader('Cache-Control', 'no-store')
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        const host = request.headers.host || '', origin = request.headers.origin
        const site = request.headers['sec-fetch-site']
        if (request.method !== 'GET' || !['127.0.0.1:5173', 'localhost:5173'].includes(host)
          || (origin && origin !== `http://${host}`)
          || (site && !['same-origin', 'none'].includes(String(site)))) {
          response.statusCode = 403
          response.end(JSON.stringify({ error: 'local_status_forbidden' }))
          return
        }
        const probe = async (url: string, token?: string) => {
          const result = await fetch(url, { signal: AbortSignal.timeout(3500),
            headers: token ? { Authorization: `Bearer ${token}` } : {} })
          if (!result.ok) throw new Error('health_unavailable')
          return await result.json() as Record<string, unknown>
        }
        const backend = async () => {
          const origin = process.env.ICT_BACKEND_ORIGIN || 'http://127.0.0.1:8010'
          const url = new URL(origin)
          if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
            return { state: 'unknown', label: '未检查' }
          }
          try {
            const result = await probe(`${url.origin}/health/ready`)
            return { state: result.status === 'ready' ? 'ready' : 'unavailable',
              label: result.status === 'ready' ? '正常' : '未就绪' }
          } catch { return { state: 'unavailable', label: '连接失败' } }
        }
        const agent = async () => {
          const file = process.env.ICT_LOCAL_AGENT_ENV_FILE
          if (!file) return { state: 'unknown', label: '未检查', mode: 'unknown' }
          try {
            if (!statSync(file).isFile() || statSync(file).size > 8192) throw new Error('bad_config')
            const config = new Map(readFileSync(file, 'utf8').split(/\r?\n/)
              .filter(line => line.includes('=') && !line.startsWith('#'))
              .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]))
            const token = config.get('RAG_SERVICE_TOKEN')
            if (!token) return { state: 'unknown', label: '未检查', mode: 'unknown' }
            const agentOrigin = new URL(process.env.ICT_LOCAL_AGENT_ORIGIN || 'http://127.0.0.1:9011')
            if (agentOrigin.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(agentOrigin.hostname)
              || agentOrigin.username || agentOrigin.password) throw new Error('bad_origin')
            const result = await probe(`${agentOrigin.origin}/v1/health`, token)
            const ready = result.status === 'ok' && result.schema_version === '1'
            return { state: ready ? 'ready' : 'unavailable', label: ready ? '正常' : '未就绪',
              mode: ready && ['extractive', 'live', 'mock'].includes(String(result.llm_mode))
                ? result.llm_mode : 'unknown' }
          } catch { return { state: 'unavailable', label: '连接失败', mode: 'unknown' } }
        }
        const [backendState, agentState] = await Promise.all([backend(), agent()])
        response.end(JSON.stringify({ backend: backendState, agent: agentState,
          checkedAt: new Date().toISOString() }))
      })
    },
  }
}
