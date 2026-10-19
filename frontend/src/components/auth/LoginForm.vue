<script setup lang="ts">
import { shallowRef, watch } from 'vue'
import type { LoginMode } from '@/composables/useLogin'

const mode = defineModel<LoginMode>('mode', { required: true })
const account = defineModel<string>('account', { required: true })
const password = defineModel<string>('password', { required: true })
const confirmation = defineModel<string>('confirmation', { required: true })
const token = defineModel<string>('token', { required: true })
defineProps<{ busy: boolean; error: string; authorized: boolean }>()
const emit = defineEmits<{ submit: []; continueSession: []; clearSession: [] }>()
const revealed = shallowRef(false)
const isLocal = import.meta.env.DEV
watch(mode, () => { revealed.value = false })
</script>

<template>
  <div class="login-form">
    <div class="login-methods" role="group" aria-label="选择登录方式">
      <button type="button" class="method-button" :aria-pressed="mode === 'account'" :disabled="busy" @click="mode = 'account'">手机号登录</button>
      <button type="button" class="method-button" :aria-pressed="mode === 'register'" :disabled="busy" @click="mode = 'register'">注册账号</button>
      <button type="button" class="method-button" :aria-pressed="mode === 'token'" :disabled="busy" @click="mode = 'token'">访问令牌</button>
    </div>

    <form class="credential-form" :aria-busy="busy" @submit.prevent="emit('submit')">
      <template v-if="mode !== 'token'">
        <div class="form-field">
          <label class="field-label" for="login-account">手机号</label>
          <input id="login-account" v-model="account" class="field-input" name="username" type="tel" inputmode="tel" autocomplete="username" autocapitalize="off" spellcheck="false" maxlength="20" placeholder="输入中国大陆手机号" required :disabled="busy" />
        </div>
        <div class="form-field">
          <label class="field-label" for="login-password">密码</label>
          <div class="secret-field">
            <input id="login-password" v-model="password" class="field-input" name="password" :autocomplete="mode === 'register' ? 'new-password' : 'current-password'" :type="revealed ? 'text' : 'password'" maxlength="128" :placeholder="mode === 'register' ? '设置 15—128 个字符的密码' : '输入密码'" required :disabled="busy" />
            <button class="reveal-button" type="button" :aria-pressed="revealed" :aria-label="revealed ? '隐藏密码' : '显示密码'" :disabled="busy" @click="revealed = !revealed">{{ revealed ? '隐藏' : '显示' }}</button>
          </div>
        </div>
        <div v-if="mode === 'register'" class="form-field">
          <label class="field-label" for="confirm-password">确认密码</label>
          <input id="confirm-password" v-model="confirmation" class="field-input" name="confirm-password" autocomplete="new-password" :type="revealed ? 'text' : 'password'" maxlength="128" placeholder="再次输入密码" required :disabled="busy" />
        </div>
        <p v-if="mode === 'register'" class="form-notice"><span>手机号作为账号使用，尚未通过短信验证。</span><span>密码为 15—128 个字符，建议使用容易记忆的长口令。</span></p>
        <p v-if="error" id="login-error" class="login-error" role="alert">{{ error }}</p>
        <button type="submit" class="login-primary" :disabled="busy || !account.trim() || !password || (mode === 'register' && !confirmation)">{{ busy ? '正在处理…' : mode === 'register' ? '注册并登录' : '登录' }}</button>
        <button v-if="authorized" class="clear-session" type="button" :disabled="busy" @click="emit('clearSession')">退出当前登录</button>
      </template>

      <template v-else>
        <div class="form-field">
          <label class="field-label" for="login-token">访问令牌</label>
          <div class="secret-field">
            <input id="login-token" v-model="token" class="field-input" name="access-token" :type="revealed ? 'text' : 'password'" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="256" placeholder="粘贴管理员签发的令牌" :disabled="busy" :aria-invalid="!!error" :aria-describedby="error ? 'token-help login-error' : 'token-help'" />
            <button class="reveal-button" type="button" :aria-pressed="revealed" :aria-label="revealed ? '隐藏令牌' : '显示令牌'" :disabled="busy" @click="revealed = !revealed">{{ revealed ? '隐藏' : '显示' }}</button>
          </div>
          <p id="token-help" class="field-help">使用普通用户访问令牌进入问答与历史会话。</p>
        </div>
        <p v-if="error" id="login-error" class="login-error" role="alert">{{ error }}</p>
        <button type="submit" class="login-primary" :disabled="busy || !token.trim()">{{ busy ? '正在验证…' : '验证并进入' }}</button>
        <button v-if="authorized || isLocal" type="button" class="login-secondary" :disabled="busy" @click="emit('continueSession')">{{ authorized ? '使用当前授权继续' : '使用本地授权' }}</button>
        <button v-if="authorized" class="clear-session" type="button" :disabled="busy" @click="emit('clearSession')">退出当前登录</button>
        <p class="token-privacy">令牌仅保存在本次页面会话中。</p>
      </template>
    </form>
    <p class="access-help">注册账号可使用普通用户问答功能。访问令牌仍可由管理员签发。</p>
  </div>
</template>

<style scoped>
.login-form { margin-top: 36px; }
.login-methods { display: flex; justify-content: center; gap: 28px; margin-bottom: 30px; }
.method-button { position: relative; border: 0; background: #fff; color: #000; padding: 10px 2px; font-size: 13px; }
.method-button[aria-pressed='true'] { font-weight: 650; }
.method-button[aria-pressed='true']::after { content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 2px; background: #000; }
.credential-form { display: grid; gap: 22px; text-align: left; }
.form-field { min-width: 0; }
.field-label { display: block; font-size: 13px; font-weight: 550; margin-bottom: 10px; }
.field-input { width: 100%; min-width: 0; height: 54px; padding: 0 16px; border: 1px solid #000; border-radius: 12px; color: #000; background: #fff; font-size: 14px; }
.field-input::placeholder { color: #000; opacity: 1; font-size: 13px; }
.field-input:focus { outline: none; box-shadow: 0 0 0 1px #000; }
.secret-field { position: relative; }
.secret-field .field-input { padding-right: 68px; }
.reveal-button { position: absolute; right: 8px; top: 5px; padding: 10px; min-height: 44px; border: 0; border-radius: 8px; background: #fff; color: #000; font-size: 12px; }
.field-help { margin: 10px 0 0; font-size: 12px; line-height: 1.8; }
.form-notice { margin: 0; font-size: 12px; line-height: 1.9; }
.form-notice strong, .form-notice span { display: block; }
.form-notice strong { font-weight: 550; }
.login-primary, .login-secondary { display: block; width: 100%; min-height: 52px; border-radius: 999px; border: 1px solid #000; font-size: 14px; font-weight: 550; }
.login-primary { color: #fff; background: #000; }
.login-secondary { color: #000; background: #fff; }
.login-primary:not(:disabled):hover { outline: 1px solid #000; outline-offset: 3px; }
.login-primary:disabled { color: #fff; background: #000; cursor: not-allowed; }
.login-secondary:disabled { color: #000; background: #fff; border-style: dashed; cursor: not-allowed; }
.login-error { margin: 0; padding: 12px 14px; border: 1px solid #000; border-radius: 10px; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.clear-session { justify-self: center; border: 0; color: #000; background: #fff; font-size: 12px; padding: 8px; text-decoration: underline; text-underline-offset: 4px; }
.token-privacy { margin: -8px 0 0; text-align: center; font-size: 11px; }
.access-help { margin: 26px 0 0; text-align: center; font-size: 12px; line-height: 1.8; }
.login-form :is(button, a):focus-visible { outline: 2px solid #000; outline-offset: 4px; }
@media (max-width: 480px) { .login-form { margin-top: 28px; } .credential-form { gap: 20px; } .field-input { font-size: 16px; } }
</style>
