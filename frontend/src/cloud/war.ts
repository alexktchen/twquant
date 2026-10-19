// 首頁戰情室（T-379／D-188，元件在 WarRoom.tsx）的型別與顯示用純函數（不碰 DOM，供 node 單元測試）。

export type Quote = { symbol: string; name: string; date: string; last: number; prev: number; chg: number; open: boolean; time: string | null }

export type Brief = {
  date: string
  generated_at: string
  /** 盤前晨報（06:30）或盤後快訊（21:45，T-409）；舊資料沒有這欄＝盤前 */
  edition?: 'morning' | 'evening'
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
  /** 同一天分上市、上櫃（D-199）；新表還沒有 → null；舊的伺服器沒有這欄 → undefined */
  market_split?: MarketSplit
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

/** 一個範圍（全市場、上市、上櫃）的家數與成交金額。家數類在盤中全市場掃完一輪前是 null。 */
export type Breadth = {
  up: number | null
  down: number | null
  flat: number | null
  limit_up: number | null
  limit_down: number | null
  new_high: number | null
  new_low: number | null
  amount: number
  amount_avg: number | null
  /** 盤後＝當天 ÷ 20 日平均；盤中＝到目前累計 ÷ 20 日平均的一整天 */
  amount_ratio: number | null
}

/**
 * 盤中大盤體檢（/api/warroom/live，T-397／D-198、D-199；形狀同 functions/_lib/market-live.ts LiveMarket）。
 * 舊的伺服器沒有 by_market、trend_otc、otc_prev → undefined。
 */
export type LiveMarket = Breadth & {
  date: string
  time: string
  since: string | null
  taiex_close: number | null
  taiex_chg: number | null
  taiex_prev: number | null
  otc_close: number | null
  otc_chg: number | null
  untraded: number | null
  trend: [string, number][]
  trend_otc?: [string, number][]
  otc_prev?: number | null
  by_market?: { tse: Breadth; otc: Breadth }
}

/** 收盤後的上市、上櫃（/api/warroom 的 market_split，表 market_breadth_daily）；還沒有 → null。 */
export type MarketSplit = { tse?: Breadth; otc?: Breadth } | null

/** 盤中大盤體檢多久抓一次（伺服器端的排程每分鐘才寫一份）。 */
export const MARKET_POLL_MS = 60_000

/** 收盤時間：盤中報價的時間到這裡（含）以後＝今天的收盤價。 */
const CLOSE_TIME = '13:30:00'

export type Scope = 'all' | 'tse' | 'otc'
export const SCOPE_LABEL: Record<Scope, string> = { all: '全部', tse: '上市', otc: '上櫃' }

/** 大盤體檢卡畫的那一份：close＝盤後體檢；live＝盤中；final＝今天已收盤、盤後體檢（約 17:40）還沒出來。 */
export type MarketView = {
  kind: 'close' | 'live' | 'final'
  date: string
  /** live／final 的報價時間 HH:MM；close → null */
  time: string | null
  taiex_close: number | null
  taiex_chg: number | null
  otc_close: number | null
  otc_chg: number | null
  /** 全部、上市、上櫃；分市場的資料還沒有 → 只有 all */
  scopes: { all: Breadth; tse: Breadth | null; otc: Breadth | null }
  /** 分時走勢與昨收（live／final 才有）：上市看加權、上櫃看櫃買 */
  trends: { taiex: [string, number][]; otc: [string, number][] }
  prevs: { taiex: number | null; otc: number | null }
}

const pickBreadth = (b: Breadth): Breadth => ({
  up: b.up,
  down: b.down,
  flat: b.flat,
  limit_up: b.limit_up,
  limit_down: b.limit_down,
  new_high: b.new_high,
  new_low: b.new_low,
  amount: b.amount,
  amount_avg: b.amount_avg,
  amount_ratio: b.amount_ratio,
})

/** 盤後體檢與盤中統計取比較新的那一天；同一天以盤後體檢為準（證交所正式收盤資料）。兩個都沒有 → null。 */
export function marketView(m: Market | null, live: LiveMarket | null, split: MarketSplit = null): MarketView | null {
  if (live && (!m || live.date > m.date)) {
    return {
      kind: live.time >= CLOSE_TIME ? 'final' : 'live',
      date: live.date,
      time: live.time.slice(0, 5),
      taiex_close: live.taiex_close,
      taiex_chg: live.taiex_chg,
      otc_close: live.otc_close,
      otc_chg: live.otc_chg,
      scopes: {
        all: pickBreadth(live),
        tse: live.by_market ? pickBreadth(live.by_market.tse) : null,
        otc: live.by_market ? pickBreadth(live.by_market.otc) : null,
      },
      trends: { taiex: live.trend, otc: live.trend_otc ?? [] },
      prevs: { taiex: live.taiex_prev, otc: live.otc_prev ?? null },
    }
  }
  if (!m) return null
  return {
    kind: 'close',
    date: m.date,
    time: null,
    taiex_close: m.taiex_close,
    taiex_chg: m.taiex_chg,
    otc_close: m.otc_close,
    otc_chg: m.otc_chg,
    scopes: {
      all: pickBreadth(m),
      tse: split?.tse ? pickBreadth(split.tse) : null,
      otc: split?.otc ? pickBreadth(split.otc) : null,
    },
    trends: { taiex: [], otc: [] },
    prevs: { taiex: null, otc: null },
  }
}

/** 某個範圍要畫的走勢與昨收：全部、上市看加權；上櫃看櫃買。 */
export function scopeTrend(v: MarketView, scope: Scope): { name: string; trend: [string, number][]; prev: number | null } {
  return scope === 'otc' ? { name: '櫃買', trend: v.trends.otc, prev: v.prevs.otc } : { name: '加權', trend: v.trends.taiex, prev: v.prevs.taiex }
}

/** 分時走勢的橫軸：9:00 開盤到 13:30 收盤（分鐘）。 */
export const SESSION_MINUTES = 270
const OPEN_MINUTE = 9 * 60

export type SparkGeom = {
  line: string
  /** 昨收虛線的 y；沒有昨收 → null */
  base: number | null
  last: [number, number]
  /** 今天最高、最低（走勢裡的，不含昨收）與它們的 y */
  high: { value: number; y: number }
  low: { value: number; y: number }
}

/** 分時走勢 → SVG 折線的點（寬 SESSION_MINUTES、高 h，昨收也算進上下範圍）；不到兩點 → null。 */
export function sparkPoints(trend: [string, number][], prev: number | null, h: number): SparkGeom | null {
  if (trend.length < 2) return null
  const pts = trend.map(([t, c]): [number, number] => {
    const [hh, mm] = t.split(':').map(Number)
    return [Math.min(Math.max(hh * 60 + mm - OPEN_MINUTE, 0), SESSION_MINUTES), c]
  })
  const vals = pts.map(([, c]) => c)
  const ys = vals.concat(prev !== null ? [prev] : [])
  const lo = Math.min(...ys)
  const hi = Math.max(...ys)
  const span = hi - lo || 1
  // 上下各留 2 單位，線不貼邊
  const y = (c: number) => Number((2 + ((hi - c) / span) * (h - 4)).toFixed(2))
  const xy = pts.map(([x, c]): [number, number] => [x, y(c)])
  const dayHigh = Math.max(...vals)
  const dayLow = Math.min(...vals)
  return {
    line: xy.map(([x, v]) => `${x},${v}`).join(' '),
    base: prev !== null ? y(prev) : null,
    last: xy[xy.length - 1],
    high: { value: dayHigh, y: y(dayHigh) },
    low: { value: dayLow, y: y(dayLow) },
  }
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

/** 行事曆搜尋：股號開頭相符或股名含關鍵字（不分大小寫，例：「00694」「富邦」「b」）；空字串＝全部相符。 */
export function calendarMatch(it: { stock_id: string; name: string | null }, q: string): boolean {
  const k = q.trim().toLowerCase()
  if (!k) return true
  return it.stock_id.toLowerCase().startsWith(k) || (it.name ?? '').toLowerCase().includes(k)
}

/** 名單股、追蹤股的排前面，其餘照原順序（依日期）。 */
export function mineFirst<T extends { stock_id: string }>(items: T[], mine: (sid: string) => boolean): T[] {
  return [...items.filter((x) => mine(x.stock_id)), ...items.filter((x) => !mine(x.stock_id))]
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

/** 往後數 n 個平日（只跳週末、不知道國定假日，所以呼叫端要寫「約」）。 */
export function addWeekdays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  let left = n
  while (left > 0) {
    d.setUTCDate(d.getUTCDate() + 1)
    const w = d.getUTCDay()
    if (w !== 0 && w !== 6) left -= 1
  }
  return d.toISOString().slice(0, 10)
}

/**
 * 即時公布的第一批大約哪天開始賣：公布日收盤後產生 → 隔一個交易日開盤買 → 抱滿 hold 個交易日。
 * tradingDays（模擬帳戶淨值的日期）裡已經過的交易日照實算（含國定假日停市），之後的只跳週末估。
 */
export function firstLiveExit(liveSince: string, hold: number, tradingDays: string[] = []): string {
  const after = tradingDays.filter((d) => d > liveSince).sort()
  if (after.length >= hold + 1) return after[hold]
  return addWeekdays(after.length > 0 ? after[after.length - 1] : liveSince, hold + 1 - after.length)
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
