import { fileURLToPath, URL } from 'node:url'
import { readFileSync, statSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
function localSession(): Plugin {
  return { name: 'local-user-session', apply: 'serve', configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (request.url?.split('?')[0] !== '/__local/session') return next()
      response.setHeader('Cache-Control', 'no-store'); response.setHeader('Content-Type', 'application/json')
      const host = request.headers.host || '', origin = request.headers.origin, site = request.headers['sec-fetch-site']
      if (request.method !== 'GET' || !['127.0.0.1:5173', 'localhost:5173'].includes(host)
          || (origin && origin !== `http://${host}`) || (site && !['same-origin', 'none'].includes(String(site)))) {
        response.statusCode = 403; response.end('{"error":"local_session_forbidden"}'); return
      }
      try {
        const file = process.env.ICT_LOCAL_SESSION_FILE
        if (!file || !statSync(file).isFile() || statSync(file).size > 4096) throw new Error('not configured')
        const session = JSON.parse(readFileSync(file, 'utf8'))
        if (typeof session.access_token !== 'string' || Date.parse(session.expires_at) <= Date.now()) throw new Error('expired')
        response.end(JSON.stringify({ access_token: session.access_token, expires_at: session.expires_at, is_demo: !!session.is_demo }))
      } catch { response.statusCode = 503; response.end('{"error":"local_session_unavailable"}') }
    })
  } }
}
export default defineConfig({
  plugins: [vue(), localSession()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': { target: process.env.ICT_BACKEND_ORIGIN || 'http://127.0.0.1:8010', changeOrigin: true } } },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
})
