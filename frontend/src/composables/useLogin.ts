import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loginError, loginReturnPath, verifyUserToken } from '@/api/auth'
import { clearUserSession, sessionExpiresAt, sessionState, userToken } from '@/api/http'

export type LoginMode = 'account' | 'token'

export function useLogin() {
  const route = useRoute(), router = useRouter()
  const mode = shallowRef<LoginMode>('account')
  const account = shallowRef(''), password = shallowRef(''), token = shallowRef('')
  const busy = shallowRef(false), error = shallowRef('')
  const authorized = computed(() => Boolean(userToken.value))
  const returnPath = computed(() => loginReturnPath(route.query.returnTo))
  let controller: AbortController | undefined
  let active = true

  watch(mode, () => { error.value = ''; password.value = ''; token.value = '' })

  async function authorize(existing = false) {
    if (busy.value) return
    error.value = ''; busy.value = true
    controller = new AbortController()
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
      await verifyUserToken(credential, controller.signal)
      if (!active || controller.signal.aborted) return
      sessionExpiresAt.value = expiresAt
      sessionState.value = 'authorized'
      userToken.value = credential
      token.value = ''; password.value = ''
      await router.replace(returnPath.value)
    } catch (cause) {
      if (active && !controller.signal.aborted) error.value = loginError(cause)
    } finally { if (active) busy.value = false }
  }

  function submit() {
    if (mode.value === 'token') void authorize()
  }

  onBeforeUnmount(() => {
    active = false; controller?.abort()
    password.value = ''; token.value = ''
  })

  return { mode, account, password, token, busy, error, authorized, submit,
    continueSession: () => authorize(true), clearSession: () => clearUserSession(false) }
}
