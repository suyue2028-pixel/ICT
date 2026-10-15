import catImage from '@/assets/assistant-cat.png'
import boyImage from '@/assets/assistant-boy.png'
import girlImage from '@/assets/assistant-girl.png'
import dogImage from '@/assets/assistant-dog.png'
import pandaImage from '@/assets/assistant-panda.png'

export const assistantAvatars = [
  { id: 'cat', name: '小猫', description: '熟悉的黑白伙伴', image: catImage },
  { id: 'boy', name: '男孩', description: '安静陪伴的小少年', image: boyImage },
  { id: 'girl', name: '女孩', description: '向你挥手的新伙伴', image: girlImage },
  { id: 'dog', name: '小狗', description: '耐心守候的好朋友', image: dogImage },
  { id: 'panda', name: '熊猫', description: '慢慢来，也没关系', image: pandaImage },
] as const

export type AssistantAvatar = (typeof assistantAvatars)[number]
export type AssistantAvatarId = AssistantAvatar['id']
export const defaultAssistantAvatar = assistantAvatars[0]

export function findAssistantAvatar(id: unknown): AssistantAvatar | undefined {
  return assistantAvatars.find((avatar) => avatar.id === id)
}
