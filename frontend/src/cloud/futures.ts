// 小台模擬（T-450／D-220）：資料型別與純函式（盤中、復盤共用；node 單元測試 tests/cloud-futures.test.ts）。
// 資料由使用者電腦上的 research/momentum_scalping/scripts/fubon_live.py 寫進 serving.futures_paper；
// 欄位改了要兩邊一起改（那支程式的 LiveCombo.snapshot）。規則 2026-10-26 凍結：ORB 只做多 + 噪音區只放空，各 1 口小台。

export type Sleeve = 'ORB_LONG' | 'NOISE_SHORT'
export const SLEEVES: readonly Sleeve[] = ['ORB_LONG', 'NOISE_SHORT']

export type FuturesEvent = {
  time: string // HH:MM:SS（盤中時間，不是寫入時間）
  kind: 'signal' | 'fill' | 'exit' | 'check' | 'info'
  sleeve: Sleeve | null
  text: string
}

export type FuturesTrade = {
  day: string
  sleeve: Sleeve
  side: 1 | -1
  signal_time: string
  entry_time: string
  entry_price: number
  exit_time: string
  exit_price: number
  exit_reason: string
  points: number
  net_pnl: number
  source: 'live' | 'official'
}

export type SleeveState = { position: -1 | 0 | 1; entry_price: number | null; realized: number; open_pnl: number | null }

export type OrbBox = { open: number; close: number; high: number; low: number; side: -1 | 0 | 1 }

export type FuturesDay = {
  version: 1
  day: string
  source: 'fubon_live' | 'taifex_replay'
  status: 'live' | 'closed'
  contract: string
  symbol: string
  updated_at: string
  last_tick: string | null
  last_price: number | null
  prev_close: number
  open: number | null
  /** [HH:MM, 開, 高, 低, 收, 那分鐘結束時的 VWAP] */
  bars: [string, number, number, number, number, number][]
  orb: {
    state: 'waiting' | 'skipped' | 'pending' | 'open' | 'closed' | 'cancelled'
    box: OrbBox | null
    stop: number | null
    target: number | null
    entry_price: number | null
    entry_time: string | null
  }
  /** 今天九個檢查點的噪音區下緣（開盤後就知道）：[HH:MM, 下緣] */
  noise_lb: [string, number][]
  noise: { time: string; lb: number; price: number; vwap: number; target: -1 | 0 }[]
  sleeves: Record<Sleeve, SleeveState>
  events: FuturesEvent[]
  trades: FuturesTrade[]
  total: number
  official: FuturesTrade[] | null
  official_match: boolean | null
}

export type FuturesDayRow = { day: string; source: string; status: string; total: number | null; match: boolean | null }

/** 秒級復盤資料（/api/futures/seconds）：08:45:00 起有成交的每一秒；s／p／w 都是差分整數，w 是 VWAP×10。 */
export type SecondsPayload = { version: 1; day: string; t0: string; s: number[]; p: number[]; w: number[] }

/** 解開後：每一點的盤中時間 HH:MM:SS、最後成交價、VWAP。 */
export type Seconds = { label: string[]; price: number[]; vwap: number[] }

/** 子帳戶的白話名稱（地鐵告示：名字短、顏色分類）。 */
export const SLEEVE_NAME: Record<Sleeve, string> = { ORB_LONG: '開盤突破・只做多', NOISE_SHORT: '噪音區・只放空' }

/** 出場原因（同 fubon_live.py 的 REASON_TEXT）。 */
export const REASON_TEXT: Record<string, string> = {
  STOP: '停損',
  TARGET: '停利',
  EOD: '收盤前',
  EXIT: '回到下緣之上',
  FLIP: '反手',
}

// 「現在平倉的話」的估算：與 intraday_bidir_test.py 相同（小台每點 50 元、每邊滑價 1 點、手續費 30 元、期交稅十萬分之二）
const POINT_VALUE = 50
const SLIP = 1
const COMMISSION = 30
const TAX = 0.00002

/** 現在用 price 平倉的淨損益（含出場滑價、來回手續費與期交稅）。 */
export function closeNowPnl(side: 1 | -1, entry: number, price: number): number {
  const exit = price - side * SLIP
  return side * (exit - entry) * POINT_VALUE - 2 * COMMISSION - TAX * (entry + exit) * POINT_VALUE
}

/** 差分加回去。 */
function undelta(xs: number[]): number[] {
  let acc = 0
  return xs.map((x) => (acc += x))
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 解開秒級資料；三個陣列長度不一樣 → 丟錯（資料壞了，不要畫錯的圖）。 */
export function decodeSeconds(sp: SecondsPayload): Seconds {
  if (sp.s.length !== sp.p.length || sp.s.length !== sp.w.length) throw new Error(`${sp.day} 秒級資料長度不一致`)
  const base = Number(sp.t0.slice(0, 2)) * 3600 + Number(sp.t0.slice(3, 5)) * 60 + Number(sp.t0.slice(6, 8))
  const label = undelta(sp.s).map((k) => {
    const t = base + k
    return `${pad(Math.floor(t / 3600))}:${pad(Math.floor(t / 60) % 60)}:${pad(t % 60)}`
  })
  return { label, price: undelta(sp.p), vwap: undelta(sp.w).map((v) => v / 10) }
}

/** 已排序的 HH:MM:SS 裡，最後一個 ≤ cut 的位置；都比 cut 晚 → -1。 */
export function lastAtOrBefore(labels: readonly string[], cut: string): number {
  let lo = 0
  let hi = labels.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (labels[mid] <= cut) {
      ans = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return ans
}

/** HH:MM:SS 加幾秒（播放用）。 */
export function addSeconds(hhmmss: string, sec: number): string {
  const t = Number(hhmmss.slice(0, 2)) * 3600 + Number(hhmmss.slice(3, 5)) * 60 + Number(hhmmss.slice(6, 8)) + Math.floor(sec)
  return `${pad(Math.floor(t / 3600))}:${pad(Math.floor(t / 60) % 60)}:${pad(t % 60)}`
}

/** 買賣點（成交、平倉）的時間，照先後；復盤的「上一個／下一個買賣點」用。 */
export const fillTimes = (d: FuturesDay): string[] =>
  d.events.filter((e) => e.kind === 'fill' || e.kind === 'exit').map((e) => e.time.slice(0, 8))

/** HH:MM → 當天第幾分鐘。 */
export const minuteOf = (hhmm: string): number => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5))

/** 圖表的時間軸：把台北的盤中時間（HH:MM 或 HH:MM:SS）當成 UTC（圖表元件用 UTC 顯示，這樣軸上就是台北時間）。 */
export function chartTime(day: string, time: string): number {
  const [y, m, d] = day.split('-').map(Number)
  const sec = time.length >= 8 ? Number(time.slice(6, 8)) : 0
  return Date.UTC(y, m - 1, d, Number(time.slice(0, 2)), Number(time.slice(3, 5)), sec) / 1000
}

/** 盤中時間有沒有在 cut 之前：cut 是 HH:MM（含那一分鐘）或 HH:MM:SS（含那一秒），比到 cut 的精度。 */
const within = (time: string, cut: string): boolean => time.slice(0, cut.length) <= cut

export type ReplayView = {
  cut: string
  bars: FuturesDay['bars']
  events: FuturesEvent[]
  price: number | null
  /** 每個子帳戶在 cut 那一刻：部位、進場價、已實現、現在平倉的話 */
  sleeves: Record<Sleeve, SleeveState>
  total: number
  /** 已經發生的成交（圖上的標記） */
  fills: { time: string; sleeve: Sleeve; side: 1 | -1; price: number; kind: 'entry' | 'exit' }[]
}

/**
 * 復盤：拉到 cut（HH:MM 或 HH:MM:SS）那一刻看得到什麼。只用 cut 之前的 K 棒、事件與成交，沒有偷看後面。
 * 秒級時：價格用那一秒以前最後一筆；那一分鐘還沒走完的 K 棒不算（它含了 cut 之後的成交）。
 * 還在盤中的那一天，最後一筆還沒出場的部位在 trades 裡沒有，從 sleeves 補上。
 */
export function replayAt(d: FuturesDay, cut: string, secs: Seconds | null = null): ReplayView {
  const bars = d.bars.filter((b) => (cut.length > 5 ? b[0] < cut.slice(0, 5) : b[0] <= cut))
  let price = bars.length ? bars[bars.length - 1][4] : null
  if (secs !== null) {
    const i = lastAtOrBefore(secs.label, cut)
    price = i >= 0 ? secs.price[i] : null
  }
  const fills: ReplayView['fills'] = []
  const sleeves = {} as Record<Sleeve, SleeveState>
  for (const s of SLEEVES) {
    let realized = 0
    let open: { side: 1 | -1; entry: number } | null = null
    for (const t of d.trades.filter((x) => x.sleeve === s)) {
      if (within(t.entry_time, cut)) fills.push({ time: t.entry_time, sleeve: s, side: t.side, price: t.entry_price, kind: 'entry' })
      if (within(t.exit_time, cut)) {
        realized += t.net_pnl
        fills.push({ time: t.exit_time, sleeve: s, side: t.side, price: t.exit_price, kind: 'exit' })
      } else if (within(t.entry_time, cut)) open = { side: t.side, entry: t.entry_price }
    }
    const live = d.sleeves[s]
    if (open === null && live.position !== 0 && live.entry_price !== null) {
      const entered = [...d.events].reverse().find((e) => e.sleeve === s && e.kind === 'fill')
      if (entered && within(entered.time, cut)) {
        open = { side: live.position as 1 | -1, entry: live.entry_price }
        fills.push({ time: entered.time, sleeve: s, side: open.side, price: open.entry, kind: 'entry' })
      }
    }
    const openPnl = open && price !== null ? Math.round(closeNowPnl(open.side, open.entry, price)) : null
    sleeves[s] = { position: open ? open.side : 0, entry_price: open ? open.entry : null, realized: Math.round(realized), open_pnl: openPnl }
  }
  const total = SLEEVES.reduce((a, s) => a + sleeves[s].realized + (sleeves[s].open_pnl ?? 0), 0)
  fills.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
  return { cut, bars, events: d.events.filter((e) => within(e.time, cut)), price, sleeves, total, fills }
}

/** 台北現在幾點幾分（不管瀏覽器在哪個時區）。 */
export function taipeiNow(now: Date): { day: string; hhmm: string } {
  const t = new Date(now.getTime() + 8 * 3600_000)
  const iso = t.toISOString()
  return { day: iso.slice(0, 10), hhmm: iso.slice(11, 16) }
}

/** 盤中模式要不要每 15 秒重抓：今天、08:25～13:50（台北）之間。 */
export function shouldPoll(now: Date, dayShown: string | null): boolean {
  const { day, hhmm } = taipeiNow(now)
  return (dayShown === null || dayShown === day) && hhmm >= '08:25' && hhmm <= '13:50'
}

/** 資料多久沒更新（秒）；寫入時間壞掉 → null。 */
export function staleSeconds(updatedAt: string, now: Date): number | null {
  const t = Date.parse(updatedAt)
  return Number.isNaN(t) ? null : Math.max(0, Math.round((now.getTime() - t) / 1000))
}

/** 盤中超過這麼久沒更新，就提醒「電腦上的錄製程式可能斷了」。 */
export const STALE_AFTER_S = 60

/** 元：+12,345／-6,789（正負號寫法同 format.ts 的 signedPrice）。 */
export function yuan(v: number): string {
  const r = Math.round(v)
  if (r === 0) return '0'
  return `${r > 0 ? '+' : '-'}${Math.abs(r).toLocaleString('en-US')}`
}

/** 漲跌色：台股漲紅跌綠。 */
export const tone = (v: number | null): 'up' | 'down' | 'plain' => (v === null || v === 0 ? 'plain' : v > 0 ? 'up' : 'down')
