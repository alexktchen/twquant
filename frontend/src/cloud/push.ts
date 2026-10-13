// 網頁推播（T-348）：右上角 🔔。每個交易日名單寫好後（約下午 5 點半）通知開了的裝置。
// iPhone 要先用 Safari「分享 → 加入主畫面」，從主畫面打開才能開通知（iOS 16.4 起）。
// 這個檔只放純函式（node 單元測試用）；開關通知的瀏覽器程式在 push-client.ts。
/** 推播的公開金鑰（與 configs/push.yaml 相同，tests/test_push_notify.py 守著；私密那半只在 GitHub secret）。 */
export const VAPID_PUBLIC_KEY = 'BCj5A9F92nMUJyViSXzW6_Z64i26W_fvID4SiyAtgzGzKhiwYpqUinQE4-uRRKZ8jfB-Cc6e9_7rWVUSYyJ8onc'

export type PushState = 'unsupported' | 'needs-install' | 'denied' | 'off' | 'on'

/** 這台裝置的通知狀態（純函式）。iPhone 沒從主畫面打開時瀏覽器根本沒有推播功能，要先提示加入主畫面。 */
export function pushStateOf(env: {
  sw: boolean
  push: boolean
  notification: boolean
  ios: boolean
  standalone: boolean
  permission: string
  subscribed: boolean
}): PushState {
  if (env.ios && !env.standalone) return 'needs-install'
  if (!env.sw || !env.push || !env.notification) return 'unsupported'
  if (env.permission === 'denied') return 'denied'
  return env.subscribed && env.permission === 'granted' ? 'on' : 'off'
}

/** 按 🔔 時顯示的說明。 */
export const PUSH_HINT: Record<PushState, string> = {
  'needs-install': 'iPhone 要先用 Safari 的「分享 → 加入主畫面」，再從主畫面打開 twquant，才能開通知。',
  unsupported: '這個瀏覽器不支援網頁通知。',
  denied: '通知被關掉了：到手機的「設定 → 通知 → twquant」打開，再回來按一次 🔔。',
  off: '開啟通知：每個交易日名單寫好後（約下午 5 點半）通知你。',
  on: '已開啟通知：每個交易日名單寫好後會通知你。再按一次關閉。',
}

/** base64url → 位元組（瀏覽器訂閱推播要的公開金鑰格式）。 */
export function base64UrlToBytes(s: string): Uint8Array<ArrayBuffer> {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4)
  const raw = atob(b64)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}
