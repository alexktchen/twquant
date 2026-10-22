// 登入狀態判斷與沒登入時的預覽資料（純函式，方便測試）。
// 登入是 Supabase Auth（T-439／D-210，session-client.ts）：每個 /api 請求帶 Bearer 權杖，沒登入或過期時 API 回 401。
// 過渡期 /api 可能還在 Cloudflare Access 後面：它會把請求導去登入頁，前端用 redirect: 'manual' 攔下這個導向。
import type { Pick, PicksDay } from './format'

/** 本站的深色登入／註冊頁（T-432；T-439 起由 LoginPage.tsx 畫）。 */
export const LOGIN_PAGE = '/login'

export class LoginRequired extends Error {
  /** redirect＝被 Cloudflare Access 導走（管理者還沒關掉 /api 的 Access）；denied＝API 回 401／403（沒登入或權杖失效） */
  readonly reason: 'redirect' | 'denied'
  constructor(reason: 'redirect' | 'denied') {
    super('需要登入')
    this.name = 'LoginRequired'
    this.reason = reason
  }
}

/** 回應是不是「要先登入」：Access 的導向（manual 模式下是 opaqueredirect）、401／403，或成功卻拿到 HTML 登入頁。 */
export function needsLogin(res: { type: string; status: number; ok: boolean; headers: Headers }): boolean {
  if (res.type === 'opaqueredirect' || res.status === 401 || res.status === 403) return true
  return res.ok && !(res.headers.get('content-type') ?? '').includes('json')
}

/** 被擋的原因（給 LoginRequired）：401／403 是被拒，其餘是被導去登入頁。 */
export const loginReason = (res: { status: number }): 'redirect' | 'denied' => (res.status === 401 || res.status === 403 ? 'denied' : 'redirect')

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
