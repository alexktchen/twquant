import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { PUSH_HOSTS, PUSH_MAX_PER_USER, parseSubscription, pushHostAllowed } from '../functions/_lib/push.ts'
import { PUSH_HINT, VAPID_PUBLIC_KEY, base64UrlToBytes, pushStateOf } from '../src/cloud/push.ts'

const env = {
  sw: true,
  push: true,
  notification: true,
  ios: false,
  standalone: false,
  permission: 'default',
  subscribed: false,
}

test('T-348 AC6: iPhone 沒從主畫面打開 → 先提示加入主畫面；其他依權限與訂閱判斷', () => {
  assert.equal(pushStateOf({ ...env, ios: true, sw: false, push: false }), 'needs-install')
  assert.equal(pushStateOf({ ...env, ios: true, standalone: true }), 'off')
  assert.equal(pushStateOf({ ...env, push: false }), 'unsupported')
  assert.equal(pushStateOf({ ...env, permission: 'denied' }), 'denied')
  assert.equal(pushStateOf({ ...env, permission: 'granted', subscribed: true }), 'on')
  // 有訂閱但權限被收回：當成沒開
  assert.equal(pushStateOf({ ...env, permission: 'default', subscribed: true }), 'off')
  assert.ok(PUSH_HINT['needs-install'].includes('加入主畫面'))
  assert.ok(PUSH_HINT.denied.includes('設定'))
})

test('T-348 AC6: 公開金鑰解得出 65 位元組的 P-256 公鑰（0x04 開頭）', () => {
  const key = base64UrlToBytes(VAPID_PUBLIC_KEY)
  assert.equal(key.length, 65)
  assert.equal(key[0], 4)
})

const sub = (endpoint: string, p256dh = 'B'.repeat(87), auth = 'a'.repeat(22)) => ({ endpoint, keys: { p256dh, auth } })

test('T-348 AC7: 只收已知推播服務的 https 網址與格式正確的金鑰', () => {
  assert.deepEqual(parseSubscription(sub('https://web.push.apple.com/QGx')), {
    endpoint: 'https://web.push.apple.com/QGx',
    p256dh: 'B'.repeat(87),
    auth: 'a'.repeat(22),
  })
  assert.ok(parseSubscription(sub('https://fcm.googleapis.com/fcm/send/abc')))
  assert.ok(parseSubscription(sub('https://wns2-bl2p.notify.windows.com/w/?token=x')))
  assert.equal(parseSubscription(sub('http://web.push.apple.com/x')), null)
  assert.equal(parseSubscription(sub('https://evil.example/x')), null)
  assert.equal(parseSubscription(sub('https://web.push.apple.com.evil.example/x')), null)
  assert.equal(parseSubscription(sub('https://web.push.apple.com/x', 'short')), null)
  assert.equal(parseSubscription(sub('https://web.push.apple.com/x', 'B'.repeat(87), 'bad auth!!')), null)
  assert.equal(parseSubscription(sub(`https://web.push.apple.com/${'x'.repeat(1000)}`)), null)
  assert.equal(parseSubscription(null), null)
  assert.equal(parseSubscription({ endpoint: 'https://web.push.apple.com/x' }), null)
  assert.equal(pushHostAllowed('not a url'), false)
  assert.ok(PUSH_HOSTS.length >= 4 && PUSH_MAX_PER_USER >= 3)
})

test('T-348 AC8: 背景程式只處理通知，不攔網路請求（不做離線快取，登出後不能留舊名單）', () => {
  const sw = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')
  assert.ok(sw.includes("addEventListener('push'"))
  assert.ok(sw.includes("addEventListener('notificationclick'"))
  assert.ok(!sw.includes("addEventListener('fetch'"))
  assert.ok(!sw.includes('caches.'))
})
