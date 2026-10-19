import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loginError, loginReturnPath, signInAccount } from '@/api/auth'
import { clearUserSession, sessionExpiresAt, userToken, authorizeUser, beginUserAuthorization, http } from '@/api/http'
import { accountInputError, normalizedPhone } from '@/utils/accountCredentials'
import { saveQuestionDraft, saveFailedQuestion } from '@/utils/queryDraft'

export type LoginMode = 'account' | 'register' | 'token'

export function useLogin() {
  const route = useRoute(), router = useRouter()
  const mode = shallowRef<LoginMode>(route.query.mode === 'register' ? 'register' : 'account')
  const account = shallowRef(''), password = shallowRef(''), token = shallowRef('')
  const confirmation = shallowRef('')
  const busy = shallowRef(false), error = shallowRef('')
  const authorized = computed(() => Boolean(userToken.value))
  const returnPath = computed(() => loginReturnPath(route.query.returnTo))
  let controller: AbortController | undefined
  let active = true

  watch(mode, () => { error.value = ''; password.value = ''; confirmation.value = ''; token.value = '' })

  async function submitAccount() {
    if (busy.value) return
    error.value = accountInputError(account.value, password.value, mode.value === 'register' ? confirmation.value : undefined)
    if (error.value) return
    busy.value = true; controller = new AbortController()
    try {
      await signInAccount(normalizedPhone(account.value), password.value, mode.value === 'register', controller.signal)
      if (!active || controller.signal.aborted) return
      // Chat history already keys by the user token. These older draft keys
      // are scope-only and must not carry another account's draft across login.
      for (const scope of [false, true]) { saveQuestionDraft(scope, ''); saveFailedQuestion(scope, '') }
      password.value = ''; confirmation.value = ''
      await router.replace(returnPath.value)
    } catch (cause) {
      if (active && !controller.signal.aborted) error.value = loginError(cause)
    } finally { if (active) busy.value = false }
  }

  async function authorize(existing = false) {
    if (busy.value) return
    error.value = ''; busy.value = true
    controller = new AbortController()
    const attempt = beginUserAuthorization()
    try {
      let expiresAt = existing ? sessionExpiresAt.value : null
      let credential = (existing ? userToken.value : token.value).trim()
      if (existing && !credential && import.meta.env.DEV) {
        const response = await fetch('/__local/session', { cache: 'no-store', signal: controller.signal })
        if (!response.ok) throw new Error('local_session_unavailable')
        const session = await response.json() as { access_token?: unknown; expires_at?: string }
        if (typeof session.access_token !== 'string' || !(Date.parse(session.expires_at || '') > Date.now())) {
          throw new Error('local_session_unavailable')
        }
        credential = session.access_token
        expiresAt = session.expires_at || null
      }
      await authorizeUser(credential, expiresAt, attempt, controller.signal)
      if (!active || controller.signal.aborted) return
      token.value = ''; password.value = ''
      await router.replace(returnPath.value)
    } catch (cause) {
      if (active && !controller.signal.aborted) error.value = loginError(cause)
    } finally { if (active) busy.value = false }
  }

  function submit() {
    if (mode.value === 'token') void authorize()
    else void submitAccount()
  }

  async function clearSession() {
    if (busy.value) return
    busy.value = true; error.value = ''
    try {
      await http.post('/auth/logout')
      clearUserSession(false)
      for (const scope of [false, true]) { saveQuestionDraft(scope, ''); saveFailedQuestion(scope, '') }
    } catch {
      error.value = '退出未完成，请稍后重试；也可刷新页面清除本地登录。'
    } finally { busy.value = false }
  }

  onBeforeUnmount(() => {
    active = false; controller?.abort()
    password.value = ''; confirmation.value = ''; token.value = ''
  })

  return { mode, account, password, confirmation, token, busy, error, authorized, submit,
    continueSession: () => authorize(true), clearSession }
}
