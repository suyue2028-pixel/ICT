<script setup lang="ts">
import catImage from '@/assets/assistant-cat.png'

defineProps<{ waiting: boolean }>()
</script>

<template>
  <div :class="['assistant-mascot', { waiting }]">
    <div class="mascot-circle">
      <img :src="catImage" width="80" height="80" alt="黑白小猫 AI 情报助手" />
    </div>
  </div>
</template>

<style scoped>
.assistant-mascot {
  display: grid;
  justify-items: center;
  width: 80px;
  padding-top: 5px;
}
.mascot-circle {
  display: grid;
  place-items: center;
  width: 80px;
  height: 80px;
  border: 1px solid #000;
  border-radius: 50%;
  background: #fff;
}
.mascot-circle img {
  display: block;
  width: 74px;
  height: 74px;
  object-fit: contain;
}
.assistant-mascot.waiting img {
  animation: mascot-breathing 2s ease-in-out infinite;
}
@keyframes mascot-breathing {
  50% {
    transform: translateY(-2px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .assistant-mascot.waiting img {
    animation: none;
  }
}
@media (max-width: 760px) {
  .assistant-mascot {
    width: 56px;
    padding-top: 0;
  }
  .mascot-circle {
    width: 56px;
    height: 56px;
  }
  .mascot-circle img {
    width: 50px;
    height: 50px;
  }
}
</style>
