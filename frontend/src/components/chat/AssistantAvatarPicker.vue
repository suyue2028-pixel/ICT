<script setup lang="ts">
import { shallowRef, useId, useTemplateRef } from 'vue'
import { assistantAvatars, type AssistantAvatarId } from '@/config/assistantAvatars'

defineProps<{ selectedId: AssistantAvatarId }>()
const emit = defineEmits<{ select: [id: AssistantAvatarId] }>()
const dialog = useTemplateRef<HTMLDialogElement>('dialog')
const titleId = useId()
const descriptionId = useId()
const opened = shallowRef(false)

function openPicker() {
  opened.value = true
  dialog.value?.showModal()
}

function choose(id: AssistantAvatarId) {
  emit('select', id)
  dialog.value?.close()
}
</script>

<template>
  <button class="avatar-picker-trigger" type="button" aria-haspopup="dialog" @click="openPicker">
    更换形象
  </button>
  <dialog
    ref="dialog"
    class="avatar-picker"
    :aria-labelledby="titleId"
    :aria-describedby="descriptionId"
    @close="opened = false"
  >
    <div class="avatar-picker-heading">
      <h2 :id="titleId" class="avatar-picker-title">选择助手形象</h2>
      <button class="avatar-picker-close" type="button" autofocus @click="dialog?.close()">
        关闭
      </button>
    </div>
    <p :id="descriptionId" class="avatar-picker-description">找一个喜欢的伙伴，陪你查资料。</p>
    <div v-if="opened" class="avatar-options" role="group" aria-label="可选助手形象">
      <button
        v-for="avatar in assistantAvatars"
        :key="avatar.id"
        class="avatar-option"
        type="button"
        :aria-label="`使用${avatar.name}形象`"
        :aria-pressed="selectedId === avatar.id"
        @click="choose(avatar.id)"
      >
        <span class="avatar-option-preview"
          ><img :src="avatar.image" width="104" height="120" alt="" decoding="async"
        /></span>
        <strong class="avatar-option-name">{{ avatar.name }}</strong>
        <span class="avatar-option-description">{{ avatar.description }}</span>
        <span class="avatar-option-state">{{
          selectedId === avatar.id ? '当前使用' : '选择这个'
        }}</span>
      </button>
    </div>
    <p class="avatar-picker-note">选择会保存在当前浏览器，仅改变助手外观。</p>
  </dialog>
</template>

<style scoped>
.avatar-picker-trigger {
  padding: 7px 0;
  border: 0;
  border-bottom: 1px solid #000;
  background: #fff;
  color: #000;
  font-size: 11px;
  line-height: 1.5;
  white-space: nowrap;
}
.avatar-picker {
  width: min(780px, calc(100% - 32px));
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 28px;
  border: 1px solid #000;
  border-radius: 22px;
  background: #fff;
  color: #000;
  overflow-y: auto;
}
.avatar-picker::backdrop {
  background: rgb(0 0 0 / 30%);
}
.avatar-picker-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.avatar-picker-title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
}
.avatar-picker-close {
  padding: 7px 13px;
  border: 1px solid #000;
  border-radius: 20px;
  background: #fff;
  color: #000;
  font-size: 12px;
}
.avatar-picker-description {
  margin: 10px 0 24px;
  font-size: 13px;
  line-height: 1.7;
}
.avatar-options {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.avatar-option {
  display: grid;
  justify-items: center;
  align-content: start;
  min-width: 0;
  padding: 12px 8px 10px;
  border: 1px solid #000;
  border-radius: 14px;
  background: #fff;
  color: #000;
}
.avatar-option[aria-pressed='true'] {
  outline: 2px solid #000;
  outline-offset: 2px;
}
.avatar-option-preview {
  display: grid;
  place-items: center;
  width: 100%;
  height: 120px;
}
.avatar-option-preview img {
  display: block;
  width: 100%;
  height: 120px;
  object-fit: contain;
}
.avatar-option-name {
  margin-top: 12px;
  font-size: 14px;
  font-weight: 600;
}
.avatar-option-description {
  margin-top: 6px;
  font-size: 10px;
  line-height: 1.7;
}
.avatar-option-state {
  display: block;
  margin-top: 14px;
  padding: 4px 9px;
  border: 1px solid #000;
  border-radius: 20px;
  font-size: 10px;
}
.avatar-option[aria-pressed='true'] .avatar-option-state {
  background: #000;
  color: #fff;
}
.avatar-option:hover .avatar-option-name {
  text-decoration: underline;
  text-underline-offset: 4px;
}
.avatar-picker-note {
  margin: 24px 0 0;
  font-size: 11px;
  line-height: 1.7;
}
.avatar-picker :is(button):focus-visible,
.avatar-picker-trigger:focus-visible {
  outline: 2px solid #000;
  outline-offset: 4px;
}
@media (max-width: 600px) {
  .avatar-picker {
    padding: 22px 18px;
    border-radius: 18px;
  }
  .avatar-picker-title {
    font-size: 20px;
  }
  .avatar-picker-description {
    margin: 8px 0 16px;
  }
  .avatar-options {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }
  .avatar-option {
    padding: 10px 8px;
  }
  .avatar-option:last-child {
    grid-column: 1 / -1;
  }
  .avatar-option-preview {
    height: 80px;
  }
  .avatar-option-preview img {
    max-width: 104px;
    height: 80px;
  }
  .avatar-option-name {
    margin-top: 6px;
  }
  .avatar-option-description {
    display: none;
  }
  .avatar-option-state {
    margin-top: 8px;
  }
  .avatar-picker-note {
    margin-top: 18px;
  }
}
</style>
