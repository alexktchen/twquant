// 登入狀態判斷與沒登入時的預覽資料（純函式，方便測試）。
// 資料 API 受 Cloudflare Access 保護：沒登入或登入過期時會被導去登入頁。
// 前端用 redirect: 'manual' 攔下這個導向，畫面改成模糊的假名單＋登入按鈕。
import type { Pick, PicksDay } from './format'

export const LOGIN_URL = '/api/login'

export class LoginRequired extends Error {
  constructor() {
    super('需要登入')
    this.name = 'LoginRequired'
  }
}

/** 回應是不是「要先登入」：Access 的導向（manual 模式下是 opaqueredirect）、401／403，或成功卻拿到 HTML 登入頁。 */
export function needsLogin(res: { type: string; status: number; ok: boolean; headers: Headers }): boolean {
  if (res.type === 'opaqueredirect' || res.status === 401 || res.status === 403) return true
  return res.ok && !(res.headers.get('content-type') ?? '').includes('json')
}

/** Cloudflare Access 的登入網域（Zero Trust 組織）。 */
export const ACCESS_TEAM = 'https://twquant-pages.cloudflareaccess.com'

/** 登入網域的登出：刪掉那邊的登入 cookie（CF_Authorization、CF_Session…）再導回本站首頁。
 *  只清本站 cookie 的話，那邊會留著已撤銷的登入；下次按「登入」，Cloudflare 拿它換通行證，
 *  換到的是作廢的，就跳出「Current authentication token is expired」（2026-10-13 使用者回報）。
 *  沒有 cookie 時一樣直接導回來，不會出現錯誤頁（2026-10-13 實測）。 */
export const teamLogout = (origin: string): string =>
  `${ACCESS_TEAM}/cdn-cgi/access/logout?returnTo=${encodeURIComponent(`${origin}/`)}`

/** 按登出後要去哪：
 *  - 伺服器撤銷登入成功，或登入早就過期（Access 把登出請求導去登入頁、或 401／403）→ 登入網域登出後回首頁；
 *  - 其他失敗（撤銷失敗、斷線）→ 本站的 Cloudflare 登出頁，確保還登入著的人一定登得出去。 */
export function logoutDestination(
  res: { type: string; status: number; ok: boolean; headers: Headers } | null,
  origin: string,
): string {
  if (res === null) return '/cdn-cgi/access/logout'
  if (res.ok || needsLogin(res)) return teamLogout(origin)
  return '/cdn-cgi/access/logout'
}

const GUEST_NAMES = ['登入後查看', '範例股票', '示範名單', '模型選股', '每日更新', '收盤產生', '範例資料', '登入查看', '示範股票', '範例名單']

function rng(seed: number): () => number {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) % 2147483648
    return s / 2147483648
  }
}

/** 把字串變成固定種子：同一天同一條線永遠是同一組假資料，切換時畫面會跟著變。 */
function seedOf(text: string): number {
  let h = 2166136261
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0
  return h % 2147483648
}

/** 沒登入時模糊的假名單：代號 0001 起（台股沒有這些代號）、名稱是說明文字，和真實股票無關。 */
export function guestPicks(n = 15, seed = 20261010): Pick[] {
  const r = rng(seed)
  return Array.from({ length: n }, (_, i) => {
    const recent = Array.from({ length: 6 }, () => r() > 0.4)
    return {
      rank: i + 1,
      sid: String(i + 1).padStart(4, '0'),
      name: GUEST_NAMES[i % GUEST_NAMES.length],
      score: null,
      close: Math.round((20 + r() * 480) * 10) / 10,
      chg: Math.round((r() * 0.09 - 0.035) * 10000) / 10000,
      streak: recent.filter(Boolean).length,
      new: i % 4 === 2,
      recent,
      chg6: Math.round((r() * 0.3 - 0.08) * 1000) / 1000,
    }
  })
}

export const GUEST_LINES = [
  { code: 'CORE', name: '大盤精選', n: 15 },
  { code: 'QUIET', name: '潛伏飆股', n: 15 },
  { code: 'QUIETV', name: '高分爆量', n: 3 }, // D-174：多數日子很少或沒有
]

/** 沒登入時某一天的假名單（三條線，股票全是假的）；日期是公開的真實交易日，股票全是假的。 */
export function guestDay(date: string): PicksDay {
  return {
    date,
    lines: GUEST_LINES.map(({ n, ...l }) => ({ ...l, picks: guestPicks(n, seedOf(`${date}:${l.code}`)) })),
  }
}

export type GuestCandle = { o: number; h: number; l: number; c: number }

/** 模糊背景裡的假 K 線（隨機漫步，固定種子）。 */
export function guestCandles(n = 48): GuestCandle[] {
  const r = rng(1010)
  let c = 100
  return Array.from({ length: n }, () => {
    const o = c
    c = Math.max(20, o * (1 + (r() - 0.46) * 0.06))
    return { o, c, h: Math.max(o, c) * (1 + r() * 0.015), l: Math.min(o, c) * (1 - r() * 0.015) }
  })
}
