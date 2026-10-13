// 網頁推播的訂閱（T-348）：開了 🔔 的裝置存在 Supabase 的 web.push_subscriptions；
// 雲端排程寫好名單後由 Python 發送（src/twquant/monitoring/push_notify.py）。純函數放這裡供 node 單元測試。

/** 只接受這些推播服務的網址（與 configs/push.yaml allowed_hosts 相同，tests/test_push_notify.py 守著）：
 *  排程會對每個訂閱網址發請求，不擋的話任何人都能登記一個網址讓我們替他打。 */
export const PUSH_HOSTS = ['web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'notify.windows.com']
/** 每個帳號最多幾台裝置（手機、平板、電腦…）。 */
export const PUSH_MAX_PER_USER = 10
/** 訂閱內容最多幾個字（推播網址約 200～500 字）。 */
export const PUSH_BODY_MAX = 2000

/** 第一次有人開通知時才建表（冪等）。同一個推播網址只屬於一個帳號：換人登入就換成新的人。 */
export const PUSH_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  `CREATE TABLE IF NOT EXISTS web.push_subscriptions (
    endpoint text PRIMARY KEY,
    email text NOT NULL,
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
  )`,
  'CREATE INDEX IF NOT EXISTS push_subscriptions_email ON web.push_subscriptions (email)',
]

export function pushHostAllowed(endpoint: string): boolean {
  let u: URL
  try {
    u = new URL(endpoint)
  } catch {
    return false
  }
  return u.protocol === 'https:' && PUSH_HOSTS.some((h) => u.hostname === h || u.hostname.endsWith(`.${h}`))
}

// 瀏覽器 PushSubscription.toJSON() 的金鑰是 base64url（p256dh 65 位元組≈87 字、auth 16 位元組≈22 字）
const B64URL = /^[A-Za-z0-9_-]+={0,2}$/

/** 瀏覽器送來的訂閱（PushSubscription.toJSON()）；格式不對或不是已知推播服務 → null。 */
export function parseSubscription(body: unknown): { endpoint: string; p256dh: string; auth: string } | null {
  const b = body as { endpoint?: unknown; keys?: { p256dh?: unknown; auth?: unknown } } | null
  const endpoint = b?.endpoint
  const p256dh = b?.keys?.p256dh
  const auth = b?.keys?.auth
  if (typeof endpoint !== 'string' || typeof p256dh !== 'string' || typeof auth !== 'string') return null
  if (endpoint.length > 1000 || !pushHostAllowed(endpoint)) return null
  if (!B64URL.test(p256dh) || p256dh.length < 80 || p256dh.length > 100) return null
  if (!B64URL.test(auth) || auth.length < 16 || auth.length > 30) return null
  return { endpoint, p256dh, auth }
}
