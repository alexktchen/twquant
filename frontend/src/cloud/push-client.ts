// 網頁推播（T-348）的瀏覽器端：註冊背景程式、讀目前狀態、開／關通知。判斷規則與文字在 push.ts。
import { deletePushSubscription, savePushSubscription } from './api'
import { VAPID_PUBLIC_KEY, base64UrlToBytes, pushStateOf, type PushState } from './push'

const hasSW = () => typeof navigator !== 'undefined' && 'serviceWorker' in navigator
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true

/** 註冊背景程式（只處理通知，不做離線快取）。 */
export function registerServiceWorker(): void {
  if (hasSW()) navigator.serviceWorker.register('/sw.js').catch((e: Error) => console.warn('背景程式註冊失敗', e))
}

async function subscription(): Promise<PushSubscription | null> {
  if (!hasSW() || !('PushManager' in window)) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return reg ? reg.pushManager.getSubscription() : null
}

/** 目前狀態；已開啟的話順便把訂閱再存一次（伺服器那邊被清掉時自動補回）。 */
export async function currentPushState(): Promise<PushState> {
  const sub = await subscription()
  const state = pushStateOf({
    sw: hasSW(),
    push: typeof window !== 'undefined' && 'PushManager' in window,
    notification: typeof Notification !== 'undefined',
    ios: isIOS(),
    standalone: isStandalone(),
    permission: typeof Notification !== 'undefined' ? Notification.permission : 'default',
    subscribed: sub !== null,
  })
  if (state === 'on' && sub) await savePushSubscription(sub.toJSON())
  return state
}

/** 開通知：先要權限（iPhone 一定要在按下按鈕的當下要，不能先等別的事），再訂閱、存到伺服器。 */
export async function enablePush(): Promise<PushState> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'off'
  const reg = await navigator.serviceWorker.ready
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64UrlToBytes(VAPID_PUBLIC_KEY) }))
  await savePushSubscription(sub.toJSON())
  return 'on'
}

/** 關通知：伺服器刪掉，瀏覽器也取消訂閱。 */
export async function disablePush(): Promise<PushState> {
  const sub = await subscription()
  if (sub) {
    await deletePushSubscription(sub.endpoint)
    await sub.unsubscribe()
  }
  return 'off'
}
