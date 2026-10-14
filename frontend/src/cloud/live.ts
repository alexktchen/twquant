// 盤中即時量價（T-347）的純函式：台北時間、要不要繼續抓、把今天這根接到 K 線、狀態文字。供 node 單元測試。
// 後端資料形狀見 functions/_lib/quote.ts。
import type { Bar } from './format'

export type Quote = {
  sid: string
  date: string
  time: string | null
  open: number | null
  high: number | null
  low: number | null
  last: number | null
  prev: number | null
  /** 今天累計成交量（股） */
  volume: number | null
  limit_up: number | null
  limit_down: number | null
  auction: boolean
}

/** 盤中幾毫秒更新一次（使用者選的 10 秒；證交所本身約 5 秒一筆）。 */
export const LIVE_POLL_MS = 10_000
// 盤中時段（台北）：9:00 開盤、13:30 收盤；收盤後多抓幾分鐘拿到最後收盤價
const OPEN = '09:00:00'
const CLOSE_AUCTION = '13:25:00'
const CLOSE = '13:30:00'
const POLL_END = '13:35:00'
// 開盤後這個時間還拿到前一天的報價＝今天沒開市（假日、颱風假）
const HOLIDAY_CHECK = '09:05:00'

/** 台北現在的日期（YYYY-MM-DD）、時間（HH:MM:SS）、星期（0＝日）；使用者的裝置不一定在台北時區。 */
export function taipeiNow(at: Date = new Date()): { date: string; time: string; weekday: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Taipei',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
      weekday: 'short',
    })
      .formatToParts(at)
      .map((p) => [p.type, p.value]),
  )
  const weekday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday)
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}:${parts.second}`, weekday }
}

/** 要不要繼續每 10 秒抓：週一到五 9:00～13:35；開盤後拿到的報價全是前一天的（休市）就停。 */
export function shouldPoll(now: { date: string; time: string; weekday: number }, quoteDates: string[]): boolean {
  if (now.weekday < 1 || now.weekday > 5 || now.time < OPEN || now.time > POLL_END) return false
  return !(now.time >= HOLIDAY_CHECK && quoteDates.length > 0 && quoteDates.every((d) => d < now.date))
}

/** 今天到目前為止的那根 K 棒：資料庫還沒有今天（傍晚才更新）時才接；報價不是今天或還沒成交 → null。 */
export function liveBar(bars: Bar[], q: Quote | undefined, today: string): Bar | null {
  if (!q || q.date !== today || q.last === null || q.open === null || q.high === null || q.low === null || q.volume === null) {
    return null
  }
  const last = bars.at(-1)
  if (last && last.date >= q.date) return null
  return { date: q.date, open: q.open, high: q.high, low: q.low, close: q.last, volume: q.volume }
}

/** 價格旁的狀態：盤中＋時間、收盤撮合中、今日收盤、尚未成交；報價不是今天 → null（顯示資料庫的收盤就好）。 */
export function liveLabel(q: Quote | undefined, today: string): string | null {
  if (!q || q.date !== today) return null
  if (q.last === null) return '尚未成交'
  const t = q.time ?? ''
  if (q.auction && t >= CLOSE_AUCTION) return '收盤撮合中'
  if (t >= CLOSE) return '今日收盤'
  return t ? `盤中 ${t}` : '盤中'
}

/** 追蹤清單一列要顯示的價格：今天有成交、而且比資料庫新 → 用即時價（漲跌對昨收）；否則用資料庫的。 */
export function rowPrice(
  db: { date: string | null; close: number | null; chg: number | null },
  q: Quote | undefined,
  today: string,
): { close: number | null; chg: number | null; tag: string | null } {
  if (!q || q.date !== today || q.last === null || (db.date !== null && q.date <= db.date)) {
    return { close: db.close, chg: db.chg, tag: null }
  }
  const chg = q.prev !== null ? q.last / q.prev - 1 : null
  return { close: q.last, chg, tag: liveLabel(q, today) === '今日收盤' ? '今日收盤' : '盤中' }
}

// 台股升降單位（同 backtest/costs.py 的 _TICKS）：價格區間 → 最小跳動
const TICKS: [number, number][] = [
  [10, 0.01],
  [50, 0.05],
  [100, 0.1],
  [500, 0.5],
  [1000, 1],
]
const tick = (p: number): number => TICKS.find(([upper]) => p < upper)?.[1] ?? 5
// 浮點誤差：105.00000000001 這種要收掉，否則跟證交所給的漲停價比不相等
const tidy = (v: number): number => Math.round(v * 10000) / 10000

/** 由昨收推漲跌停價（±10% 再取到升降單位，同 costs.limit_up_proxy／limit_down_proxy）；沒有昨收 → null。 */
export function limitPrices(prev: number | null): { up: number | null; down: number | null } {
  if (prev === null || !Number.isFinite(prev) || prev <= 0) return { up: null, down: null }
  const up = prev * 1.1
  const down = prev * 0.9
  return {
    up: tidy(Math.floor(up / tick(up) + 1e-9) * tick(up)),
    down: tidy(Math.ceil(down / tick(down) - 1e-9) * tick(down)),
  }
}

/** 這個價格是不是漲停（'up'）或跌停（'down'）。漲跌停價缺 → 不判斷。 */
export function limitState(px: number | null, up: number | null, down: number | null): 'up' | 'down' | null {
  if (px === null) return null
  if (up !== null && px >= up - 1e-6) return 'up'
  if (down !== null && px <= down + 1e-6) return 'down'
  return null
}

/**
 * 每日名單一列要顯示的價格。最新一天的名單在盤中改用即時價（漲跌對昨收），並留著推薦當天的收盤（rec）；
 * 看過去某天、或還沒有今天的即時價 → 名單那天的收盤與漲跌（rec＝null，小字照舊放近 6 日漲幅）。
 * limit：漲停／跌停（即時價用證交所給的漲跌停價，收盤價用前一天收盤推）。
 */
export function pickPrice(
  p: { close: number | null; chg: number | null; prev?: number | null },
  listDate: string | undefined,
  q: Quote | undefined,
  today: string,
  past: boolean,
): { price: number | null; chg: number | null; rec: number | null; tag: string | null; limit: 'up' | 'down' | null } {
  if (!past && q && q.date === today && q.last !== null && listDate !== undefined && listDate < q.date) {
    const lim = limitPrices(q.prev)
    return {
      price: q.last,
      chg: q.prev !== null ? q.last / q.prev - 1 : null,
      rec: p.close,
      tag: liveLabel(q, today) === '今日收盤' ? '今日收盤' : '盤中',
      limit: limitState(q.last, q.limit_up ?? lim.up, q.limit_down ?? lim.down),
    }
  }
  const lim = limitPrices(p.prev ?? null)
  return { price: p.close, chg: p.chg, rec: null, tag: null, limit: limitState(p.close, lim.up, lim.down) }
}

/** 追蹤清單一列的漲跌停：只看今天的即時報價（清單沒有前收，收盤後不判斷）。 */
export function quoteLimit(q: Quote | undefined, today: string): 'up' | 'down' | null {
  if (!q || q.date !== today || q.last === null) return null
  const lim = limitPrices(q.prev)
  return limitState(q.last, q.limit_up ?? lim.up, q.limit_down ?? lim.down)
}
