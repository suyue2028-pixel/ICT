<script setup lang="ts">
import AssistantAvatarPicker from './AssistantAvatarPicker.vue'
import type { AssistantAvatar, AssistantAvatarId } from '@/config/assistantAvatars'

defineProps<{ waiting: boolean; avatar: AssistantAvatar }>()
const emit = defineEmits<{ selectAvatar: [id: AssistantAvatarId] }>()
</script>

<template>
  <div :class="['assistant-mascot', { waiting }]">
    <div :class="['mascot-circle', { 'is-person': avatar.id === 'boy' || avatar.id === 'girl' }]">
      <img :src="avatar.image" width="80" height="80" :alt="`黑白${avatar.name}情报助手`" />
    </div>
    <AssistantAvatarPicker :selected-id="avatar.id" @select="emit('selectAvatar', $event)" />
  </div>
</template>

<style scoped>
.assistant-mascot {
  display: grid;
  justify-items: center;
  gap: 10px;
  width: 80px;
  padding-top: 5px;
}
.mascot-circle {
  position: relative;
  display: grid;
  place-items: center;
  width: 80px;
  height: 80px;
  border: 1px solid #000;
  border-radius: 50%;
  background: #fff;
  overflow: hidden;
}
.mascot-circle img {
  display: block;
  width: 74px;
  height: 74px;
  object-fit: contain;
}
.mascot-circle.is-person img {
  position: absolute;
  top: -10%;
  left: 50%;
  width: 180%;
  height: auto;
  max-width: none;
  translate: -50% 0;
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
