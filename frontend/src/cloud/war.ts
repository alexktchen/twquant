// 首頁戰情室（T-379／D-188，元件在 WarRoom.tsx）的型別與顯示用純函數（不碰 DOM，供 node 單元測試）。

export type Quote = { symbol: string; name: string; date: string; last: number; prev: number; chg: number; open: boolean; time: string | null }

export type Brief = {
  date: string
  generated_at: string
  rule_text: string
  headline: string | null
  bullets: string[] | null
  risk_note: string | null
  model_id: string | null
  absent_reason: string | null
  facts: string[] | null
  extras: Quote[] | null
  adr_premium: number | null
}

export type Market = {
  date: string
  taiex_close: number | null
  taiex_chg: number | null
  otc_close: number | null
  otc_chg: number | null
  amount: number
  amount_avg: number | null
  amount_ratio: number | null
  up: number
  down: number
  flat: number
  limit_up: number
  limit_down: number
  new_high: number | null
  new_low: number | null
  foreign_net: number
  trust_net: number
  dealer_net: number
  foreign_streak: number
  margin_balance: number | null
  margin_delta: number | null
  short_balance: number | null
}

export type Taifex = {
  date: string
  fut_dealer_net_oi: number | null
  fut_trust_net_oi: number | null
  fut_foreign_net_oi: number | null
  fut_foreign_long_oi: number | null
  fut_foreign_short_oi: number | null
  pc_vol_ratio: number | null
  pc_oi_ratio: number | null
  put_oi: number | null
  call_oi: number | null
}

/** ts＝發言時間 epoch 秒；content＝說明全文（可能被截斷；存託憑證沒有）。 */
export type NewsItem = {
  stock_id: string
  name: string | null
  subject: string
  at: string
  ts: number
  tags: string[]
  listed: boolean
  content?: string | null
}

/** 一則公告的鍵（同 PG 主鍵：代號＋發言時間＋主旨）。 */
export const newsId = (n: { stock_id: string; ts: number; subject: string }): string => `${n.stock_id}|${n.ts}|${n.subject}`

/** 收起時輪播多久換一則。 */
export const CAROUSEL_MS = 6_000

/** 輪播往前／往後一則（頭尾相接）；沒有東西 → 0。 */
export function carouselStep(i: number, n: number, dir: 1 | -1): number {
  if (n <= 0) return 0
  return (((i + dir) % n) + n) % n
}

export type WarRoomData = {
  today: string
  brief: Brief | null
  market: Market | null
  taifex: Taifex | null
  picks: { date: string; lines: { code: string; name: string; n: number; new: number }[] } | null
  news: { since: string; items: NewsItem[] }
  calendar: {
    conferences: { date: string; stock_id: string; name: string | null; conf_time: string; summary: string | null }[]
    dividends: { date: string; stock_id: string; name: string | null; event_type: string }[]
    revenue: { date: string; days: number }
  }
}

const MINUS = '−'

/** 金額（TWD）→「+244 億」「−1,410 億」；null →「—」。 */
export function signedYi(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const n = Math.round(Math.abs(v) / 1e8)
  return `${v < 0 ? MINUS : '+'}${n.toLocaleString('en-US')} 億`
}

/** 外資連續天數 → 「連賣 5 天」；不到 2 天不說。 */
export function streakText(streak: number | null | undefined): string {
  if (!streak || Math.abs(streak) < 2) return ''
  return `連${streak > 0 ? '買' : '賣'} ${Math.abs(streak)} 天`
}

/** 漲跌平家數 → 百分比寬度（畫比例條）。 */
export function breadth(up: number, down: number, flat: number): { up: number; down: number; flat: number } {
  const total = up + down + flat
  if (total <= 0) return { up: 0, down: 0, flat: 0 }
  return { up: (up / total) * 100, down: (down / total) * 100, flat: (flat / total) * 100 }
}

/** 漲紅跌綠。 */
export function tone(x: number | null | undefined): 'up' | 'down' | 'flat' {
  if (x === null || x === undefined || x === 0) return 'flat'
  return x > 0 ? 'up' : 'down'
}

/** 事件代碼 → 中文。 */
export const EVENT_NAME: Record<string, string> = {
  cash_dividend: '除息',
  stock_dividend: '除權',
  cash_and_stock_dividend: '除權息',
  split: '分割',
  reverse_split: '減資',
}

/** 「2026-10-17」→「10/17（六）」。 */
export function dayLabel(day: string): string {
  const d = new Date(`${day}T00:00:00Z`)
  const w = '日一二三四五六'[d.getUTCDay()]
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}（${w}）`
}

/** PG 的時間字串「2026-10-16 23:04:00+08」→「10/16 23:04」。 */
export function atLabel(at: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(at)
  return m ? `${Number(m[2])}/${Number(m[3])} ${m[4]}:${m[5]}` : at
}
