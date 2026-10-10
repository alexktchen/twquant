// 登入狀態判斷與沒登入時的預覽資料（純函式，方便測試）。
// 資料 API 受 Cloudflare Access 保護：沒登入或登入過期時會被導去登入頁。
// 前端用 redirect: 'manual' 攔下這個導向，畫面改成模糊的假名單＋登入按鈕。
import type { Pick } from './format'

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

const GUEST_NAMES = ['登入後查看', '範例股票', '示範名單', '模型選股', '每日更新', '收盤產生', '範例資料', '登入查看', '示範股票', '範例名單']

function rng(seed: number): () => number {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) % 2147483648
    return s / 2147483648
  }
}

/** 沒登入時模糊背景裡的假名單：代號一律 0000、名稱是說明文字，和真實股票無關。 */
export function guestPicks(n = 10): Pick[] {
  const r = rng(20261010)
  return Array.from({ length: n }, (_, i) => {
    const recent = Array.from({ length: 6 }, () => r() > 0.4)
    return {
      rank: i + 1,
      sid: '0000',
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
