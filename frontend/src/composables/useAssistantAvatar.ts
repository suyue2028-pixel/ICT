import { computed, onMounted, readonly, shallowRef } from 'vue'
import {
  defaultAssistantAvatar,
  findAssistantAvatar,
  type AssistantAvatarId,
} from '@/config/assistantAvatars'

const storageKey = 'ict:assistant-avatar:v1'

export function useAssistantAvatar() {
  const selectedId = shallowRef<AssistantAvatarId>(defaultAssistantAvatar.id)
  const selectedAvatar = computed(
    () => findAssistantAvatar(selectedId.value) || defaultAssistantAvatar,
  )

  onMounted(() => {
    try {
      const saved = findAssistantAvatar(localStorage.getItem(storageKey))
      if (saved) selectedId.value = saved.id
    } catch {
      // Avatar selection remains available when browser storage is disabled.
    }
  })

  function selectAvatar(id: AssistantAvatarId) {
    const avatar = findAssistantAvatar(id)
    if (!avatar) return
    selectedId.value = avatar.id
    try {
      localStorage.setItem(storageKey, avatar.id)
    } catch {
      // Keep the current choice in memory for this visit.
    }
  }

  return { selectedId: readonly(selectedId), selectedAvatar, selectAvatar }
}
