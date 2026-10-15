export const PASSWORD_MIN_LENGTH = 15
export function normalizedPhone(value: string): string {
  const phone = value.trim().replace(/[ -]/g, '')
  return /^(?:\+86)?1[3-9][0-9]{9}$/.test(phone) ? (phone.startsWith('+86') ? phone : `+86${phone}`) : ''
}
export function accountInputError(phone: string, password: string, confirmation?: string): string {
  if (!normalizedPhone(phone)) return '请输入有效的中国大陆手机号。'
  const size = Array.from(password.normalize('NFC')).length
  if (size < (confirmation === undefined ? 1 : PASSWORD_MIN_LENGTH) || size > 128) {
    return confirmation === undefined ? '请输入密码，最多 128 个字符。' : '密码须为 15—128 个字符，建议使用长口令。'
  }
  if (confirmation !== undefined && password.normalize('NFC') !== confirmation.normalize('NFC')) return '两次输入的密码不一致。'
  return ''
}
