<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authorizeUser, clearUserSession, ensureSession, errorMessage, sessionState, userToken } from '@/api/http'
import { safeReturnPath } from '@/utils/requestFailure'
const route = useRoute(), router = useRouter()
const token = shallowRef(''), busy = shallowRef(false), error = shallowRef('')
const returnPath = computed(() => safeReturnPath(route.query.returnTo))
const isLocal = import.meta.env.DEV
async function authorize() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await authorizeUser(token.value); token.value = ''; await router.replace(returnPath.value) }
  catch (cause) { error.value = errorMessage(cause) }
  finally { busy.value = false }
}
async function recoverLocal() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await ensureSession(true)
    if (!userToken.value) error.value = '本地没有有效的用户授权。请让管理员重新签发短期用户令牌后再次获取，或在下方填写已签发的令牌。'
    else await router.replace(returnPath.value)
  } finally { busy.value = false }
}
</script>
<template>
  <section class="login-page">
    <div class="page-heading"><div><h1>用户授权</h1><p>验证平台用户身份后，可直接使用平台默认模型。</p></div></div>
    <div class="panel login-panel">
      <p>当前版本使用管理员签发的短期用户令牌。这里填写的是用户访问凭证，不是大模型 API Key。</p>
      <p v-if="sessionState === 'expired'" role="status">原用户授权已失效。已输入的问题仍保留在问答页面。</p>
      <p v-if="userToken">当前用户授权有效。<router-link :to="returnPath">返回问答</router-link>，或更换授权。</p>
      <form @submit.prevent="authorize">
        <label for="user-access-token">短期用户令牌</label>
        <input id="user-access-token" v-model="token" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="256" :disabled="busy" />
        <button class="primary-link" type="submit" :disabled="busy || !token.trim()">{{ busy ? '正在验证…' : '验证并进入' }}</button>
      </form>
      <p v-if="error" role="alert">{{ error }}</p>
      <button v-if="isLocal" class="ghost-button" type="button" :disabled="busy" @click="recoverLocal">重新获取本地用户授权</button>
      <button v-if="userToken" class="ghost-button" type="button" :disabled="busy" @click="clearUserSession(false)">清除当前用户授权</button>
      <p class="muted">令牌只保留在当前页面运行内存中，刷新页面后需再次授权。本地开发可以读取已有的有效授权；页面不能自行签发令牌。</p>
    </div>
  </section>
</template>
