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
  /** 昨晚到今早的新聞頭條（晨報 06:30 抓的快照；舊晨報沒有這欄） */
  headlines?: Headline[] | null
}

export type Headline = { news_id: number; title: string; summary: string | null; published_at: string; category: string | null; url: string; source: string }

/** 某天大盤實際收盤（晨報寫在開盤前，拿來對照）。 */
export type BriefActual = {
  date: string
  taiex_close: number | null
  taiex_chg: number | null
  otc_close: number | null
  otc_chg: number | null
  foreign_net: number | null
}

/** 日期清單（新到舊）裡，day 的前一天（較舊）與後一天（較新）；不在清單裡 → 都 null。 */
export function briefNeighbors(dates: string[], day: string): { older: string | null; newer: string | null } {
  const i = dates.indexOf(day)
  if (i < 0) return { older: null, newer: null }
  return { older: dates[i + 1] ?? null, newer: i > 0 ? dates[i - 1] : null }
}

/** 「2026-10-17T06:12:00+08:00」→「10/17 06:12」。 */
export function headlineTime(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  return m ? `${Number(m[2])}/${Number(m[3])} ${m[4]}:${m[5]}` : iso
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
  /** 有晨報的日期（新到舊），給「今日一句話」切換歷史 */
  brief_dates?: string[]
  market: Market | null
  taifex: Taifex | null
  /** sids：今天名單上的股票（三條線合起來；行事曆標「名單」用） */
  picks: { date: string; lines: { code: string; name: string; n: number; new: number }[]; sids?: string[] } | null
  news: { since: string; items: NewsItem[] }
  calendar: {
    conferences: { date: string; stock_id: string; name: string | null; conf_time: string; summary: string | null }[]
    dividends: {
      date: string
      stock_id: string
      name: string | null
      kind: 'cash' | 'stock' | 'both'
      cash_dividend: number | null
      stock_ratio: number | null
      rights_ratio: number | null
    }[]
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

/** 行事曆的一筆（除權息或法說會合在一起排）。 */
export type CalendarItem = {
  key: string
  date: string
  kind: 'cash' | 'stock' | 'both' | 'conf'
  stock_id: string
  name: string | null
  conf_time: string | null
  summary: string | null
  dividend: { cash_dividend: number | null; stock_ratio: number | null; rights_ratio: number | null } | null
}

/** 除權息與法說會合成一串，依日期、（法說會）時間、代號排。 */
export function calendarItems(cal: WarRoomData['calendar']): CalendarItem[] {
  const divs: CalendarItem[] = cal.dividends.map((d) => ({
    key: `d${d.date}${d.stock_id}`,
    date: d.date,
    kind: d.kind,
    stock_id: d.stock_id,
    name: d.name,
    conf_time: null,
    summary: null,
    dividend: { cash_dividend: d.cash_dividend, stock_ratio: d.stock_ratio, rights_ratio: d.rights_ratio },
  }))
  const confs: CalendarItem[] = cal.conferences.map((c) => ({
    key: `c${c.date}${c.stock_id}${c.conf_time}`,
    date: c.date,
    kind: 'conf',
    stock_id: c.stock_id,
    name: c.name,
    conf_time: c.conf_time,
    summary: c.summary,
    dividend: null,
  }))
  const order = (x: CalendarItem) => `${x.date}|${x.kind === 'conf' ? '1' : '0'}|${x.conf_time ?? ''}|${x.stock_id}`
  return [...divs, ...confs].sort((a, b) => (order(a) < order(b) ? -1 : order(a) > order(b) ? 1 : 0))
}

/** 每天幾檔除權息、幾場法說會（收起時的摘要）。 */
export function calendarDays(items: CalendarItem[]): { date: string; dividends: number; conferences: number }[] {
  const out = new Map<string, { date: string; dividends: number; conferences: number }>()
  for (const it of items) {
    const d = out.get(it.date) ?? { date: it.date, dividends: 0, conferences: 0 }
    if (it.kind === 'conf') d.conferences += 1
    else d.dividends += 1
    out.set(it.date, d)
  }
  return [...out.values()].sort((a, b) => (a.date < b.date ? -1 : 1))
}

/** 除權息預告的種類 → 中文。 */
export const DIV_KIND: Record<string, string> = { cash: '除息', stock: '除權', both: '除權息' }

/** 一筆除權息預告的金額說明：「現金 1.2 元」「配股 0.1」「現增 0.09」；都沒有 → 空字串。 */
export function dividendText(d: { cash_dividend: number | null; stock_ratio: number | null; rights_ratio: number | null }): string {
  const parts: string[] = []
  if (d.cash_dividend) parts.push(`現金 ${Number(d.cash_dividend.toFixed(4))} 元`)
  if (d.stock_ratio) parts.push(`配股 ${Number(d.stock_ratio.toFixed(4))}`)
  if (d.rights_ratio) parts.push(`現增 ${Number(d.rights_ratio.toFixed(4))}`)
  return parts.join('、')
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
