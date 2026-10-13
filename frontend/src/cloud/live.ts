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
