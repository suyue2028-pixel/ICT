<script setup lang="ts">
import LoginForm from '@/components/auth/LoginForm.vue'
import { useLogin } from '@/composables/useLogin'

const { mode, account, password, token, busy, error, authorized, submit, continueSession, clearSession } = useLogin()
</script>

<template>
  <div class="login-page">
    <a class="skip-link" href="#login-main">跳转到登录表单</a>
    <header class="login-header">
      <router-link to="/" class="login-brand" aria-label="安全情报工作台首页">安全情报<span>工作台</span></router-link>
      <router-link to="/" class="workspace-return">返回工作台</router-link>
    </header>
    <main id="login-main" class="login-main" tabindex="-1">
      <section class="login-content" aria-labelledby="login-heading">
        <p class="login-eyebrow">AI 安全知识情报系统</p>
        <h1 id="login-heading" class="login-heading">欢迎回来</h1>
        <p class="login-description">从一条情报开始，找到有依据的回答。</p>
        <LoginForm v-model:mode="mode" v-model:account="account" v-model:password="password" v-model:token="token"
          :busy="busy" :error="error" :authorized="authorized" @submit="submit" @continue-session="continueSession" @clear-session="clearSession" />
        <router-link to="/intelligence" class="browse-link">先浏览公开情报</router-link>
      </section>
    </main>
    <footer class="login-footer"><span>智能体驱动的 AI 安全知识情报系统</span><span>情报检索 · 来源追溯 · 知识问答</span></footer>
  </div>
</template>

<style scoped>
.login-page { display: flex; flex-direction: column; min-height: 100vh; min-height: 100dvh; background: #fff; color: #000; }
.login-header { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 28px 40px; }
.login-brand { display: flex; align-items: baseline; gap: 9px; font-size: 19px; font-weight: 650; letter-spacing: -.5px; }
.login-brand span { font-size: 12px; font-weight: 400; letter-spacing: 0; }
.workspace-return { font-size: 12px; padding: 10px 15px; border: 1px solid #000; border-radius: 999px; white-space: nowrap; }
.login-main { display: flex; align-items: center; justify-content: center; flex: 1; padding: 40px 24px 70px; }
.login-content { width: 100%; max-width: 384px; text-align: center; }
.login-eyebrow { margin: 0 0 20px; font-size: 12px; letter-spacing: 1px; }
.login-heading { font-size: clamp(30px, 4vw, 36px); line-height: 1.3; font-weight: 550; letter-spacing: -1px; margin: 0; }
.login-description { margin: 16px 0 0; font-size: 13px; line-height: 1.8; }
.browse-link { display: inline-block; padding: 8px 0; margin-top: 24px; font-size: 12px; text-decoration: underline; text-underline-offset: 4px; }
.login-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 24px 40px; font-size: 11px; line-height: 1.8; }
.login-page a:focus-visible { outline: 2px solid #000; outline-offset: 4px; }
@media (max-width: 600px) { .login-header { padding: 22px 20px; gap: 12px; } .login-brand { font-size: 17px; gap: 7px; } .workspace-return { padding: 9px 12px; font-size: 11px; } .login-main { padding: 38px 24px 48px; } .login-footer { flex-direction: column; justify-content: center; text-align: center; gap: 4px; padding: 22px 20px; } }
</style>
