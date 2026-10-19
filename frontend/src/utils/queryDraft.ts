const draftKey = (isDemo: boolean) => `ict:qa-draft:${isDemo ? 'demo' : 'real'}`
const failedKey = (isDemo: boolean) => `ict:qa-failed:${isDemo ? 'demo' : 'real'}`
function read(key: string) { try { return sessionStorage.getItem(key) || '' } catch { return '' } }
function write(key: string, value: string) {
  try { value ? sessionStorage.setItem(key, value.slice(0, 4000)) : sessionStorage.removeItem(key) } catch { /* Optional storage. */ }
}
export const readQuestionDraft = (isDemo: boolean) => read(draftKey(isDemo))
export const saveQuestionDraft = (isDemo: boolean, value: string) => write(draftKey(isDemo), value)
export const readFailedQuestion = (isDemo: boolean) => read(failedKey(isDemo))
export const saveFailedQuestion = (isDemo: boolean, value: string) => write(failedKey(isDemo), value)
