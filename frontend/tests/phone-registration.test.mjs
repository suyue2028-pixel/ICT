import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire, stripTypeScriptTypes } from 'node:module'
import { pathToFileURL } from 'node:url'
import { parse } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { accountInputError, normalizedPhone } from '../src/utils/accountCredentials.ts'

test('phone registration validates canonical phones, long passwords and confirmation', () => {
  assert.equal(normalizedPhone('+86 199-0000-0001'), '+8619900000001')
  assert.equal(normalizedPhone('not-a-phone'), '')
  assert.equal(accountInputError('19900000001', 'Fictional long phrase!', 'Fictional long phrase!'), '')
  assert.match(accountInputError('19900000001', 'short', 'short'), /15/)
  assert.match(accountInputError('19900000001', 'Fictional long phrase!', 'mismatch'), /不一致/)
  assert.equal(accountInputError('19900000001', 'Fictional café phrase', 'Fictional cafe\u0301 phrase'), '')
})

test('register form has phone, new-password confirmation, real submit and no SMS claim', async () => {
  const source = readFileSync(new URL('../src/components/auth/LoginForm.vue', import.meta.url), 'utf8')
  const render = new Function('Vue', compile(parse(source).descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
  const app = Vue.createSSRApp({ render, setup: () => ({ mode: 'register', account: '19900000001', password: 'Fictional long phrase!',
    confirmation: 'Fictional long phrase!', token: '', busy: false, error: '', authorized: false, revealed: false, isLocal: true, emit: () => {} }) })
  const html = await renderToString(app)
  assert.match(html, /创建账号|注册账号/)
  assert.match(html, /手机号/)
  assert.match(html, /确认密码/)
  assert.match(html, /autocomplete="new-password"/)
  assert.match(html, /注册并登录/)
  assert.match(html, /尚未通过短信验证/)
  assert.doesNotMatch(html, /登录（待接入）|仅供界面预览/)
})

async function sessionModule() {
  const require = createRequire(import.meta.url)
  let source = stripTypeScriptTypes(readFileSync(new URL('../src/api/http.ts', import.meta.url), 'utf8'))
  source = source.replace("'axios'", JSON.stringify(pathToFileURL(require.resolve('axios', { conditions: ['import'] })).href))
    .replace("'vue'", JSON.stringify(pathToFileURL(require.resolve('vue')).href))
    .replace("'@/utils/requestFailure'", JSON.stringify(new URL('../src/utils/requestFailure.ts', import.meta.url).href))
    .replaceAll('import.meta.env.VITE_API_BASE_URL', 'undefined').replaceAll('import.meta.env.DEV', 'true')
  return import(`data:text/javascript;base64,${Buffer.from(source + '\n// ' + Math.random()).toString('base64')}`)
}

test('late local bootstrap cannot replace a manually authenticated account', async () => {
  const values = new Map()
  globalThis.sessionStorage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) }
  const auth = await sessionModule()
  let release
  globalThis.fetch = () => new Promise(resolve => { release = resolve })
  auth.http.defaults.adapter = async config => ({ status: 200, statusText: 'OK', config, headers: {},
    data: { authenticated: true, owner_id: 'fictional-account' } })
  const bootstrap = auth.ensureSession()
  const attempt = auth.beginUserAuthorization()
  await auth.authorizeUser('fictional-new-user-token', new Date(Date.now() + 60000).toISOString(), attempt)
  release({ ok: true, json: async () => ({ access_token: 'fictional-old-local-token', expires_at: new Date(Date.now() + 60000).toISOString() }) })
  await bootstrap
  assert.equal(auth.userToken.value, 'fictional-new-user-token')
  assert.equal(values.get('ict:manual-auth'), '1')
  auth.clearUserSession(false)
  let fetched = false
  globalThis.fetch = async () => { fetched = true; throw new Error('must not bootstrap after manual sign-out') }
  await auth.ensureSession()
  assert.equal(fetched, false)
  assert.equal(auth.userToken.value, '')
})
