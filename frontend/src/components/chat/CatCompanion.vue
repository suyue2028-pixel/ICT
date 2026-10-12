<script setup lang="ts">
import { computed, onBeforeUnmount, shallowRef } from 'vue'
import catAvatar from '@/assets/assistant-avatar.svg'

const props = defineProps<{ sending: boolean }>()
const greeting = shallowRef(false)
let timer: ReturnType<typeof setTimeout> | undefined
const caption = computed(() => greeting.value ? '喵，慢慢问，我在呢。'
  : props.sending ? '陪你等回答…' : '陪你查资料的小猫')
function greet() {
  greeting.value = true
  clearTimeout(timer)
  timer = setTimeout(() => { greeting.value = false }, 2600)
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="cat-companion" aria-label="陪伴小猫">
    <button type="button" :class="['cat-button', { greeted: greeting, waiting: sending }]"
      aria-label="和小猫打个招呼" @click="greet">
      <img :src="catAvatar" width="72" height="72" alt="浅紫底的白色小猫" />
    </button>
    <div class="cat-caption"><strong aria-live="polite">{{ caption }}</strong><span>点一下，和它打个招呼</span></div>
  </section>
</template>

<style scoped>
.cat-companion { display: flex; align-items: center; gap: 12px; margin-top: 16px; padding: 14px 12px; border: 1px solid #000; border-radius: 16px; background: var(--panel-background); }
.cat-button { display: grid; place-items: center; flex: 0 0 68px; width: 68px; height: 68px; padding: 0; border: 0; border-radius: 50%; background: transparent; cursor: pointer; }
.cat-button img { display: block; width: 68px; height: 68px; transition: transform .2s ease; }
.cat-button:hover img { transform: rotate(-8deg) translateY(-2px); }
.cat-button.greeted img { animation: cat-greeting .65s ease-in-out 2; }
.cat-button.waiting img { animation: cat-breathing 2s ease-in-out infinite; }
.cat-caption { min-width: 0; font-size: 12px; line-height: 1.7; color: #000; }
.cat-caption strong, .cat-caption span { display: block; }
.cat-caption strong { font-weight: 550; }
.cat-caption span { margin-top: 3px; font-size: 10px; }
@keyframes cat-greeting { 0%, 100% { transform: rotate(0); } 35% { transform: rotate(-10deg) translateY(-3px); } 70% { transform: rotate(8deg); } }
@keyframes cat-breathing { 50% { transform: translateY(-3px); } }
@media (prefers-reduced-motion: reduce) { .cat-button img { transition: none; animation: none !important; } }
</style>
