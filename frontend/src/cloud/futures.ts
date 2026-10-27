// 小台模擬（T-450／D-220）：資料型別與純函式（盤中、復盤共用；node 單元測試 tests/cloud-futures.test.ts）。
// 資料由使用者電腦上的 research/momentum_scalping/scripts/fubon_live.py 寫進 serving.futures_paper；
// 欄位改了要兩邊一起改（那支程式的 LiveCombo.snapshot）。規則 2026-10-26 凍結：ORB 只做多 + 噪音區只放空，各 1 口小台。
// T-452：另一個策略「開高走低」（A 開高失守、B 跌破開盤區間、C 補缺口，studies/gap_fail）在同一份狀態的 gap_fail（GapFail.snapshot）。

/** 兩個策略：固定組合（ORB 只做多 + 噪音區只放空）、開高走低（A、B、C 三條只放空）。 */
export type Strategy = 'combo' | 'gap'
export const STRATEGY_NAME: Record<Strategy, string> = { combo: '固定組合', gap: '開高走低' }

export type ComboSleeve = 'ORB_LONG' | 'NOISE_SHORT'
export type GapSleeve = 'GAP_A' | 'GAP_B' | 'GAP_C'
export type Sleeve = ComboSleeve | GapSleeve
export const SLEEVES: readonly ComboSleeve[] = ['ORB_LONG', 'NOISE_SHORT']
export const GAP_SLEEVES: readonly GapSleeve[] = ['GAP_A', 'GAP_B', 'GAP_C']

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

/** 開高走低每一條的狀態（同 fubon_live.py 的 GapFail.snapshot）：none＝收盤都沒等到訊號。 */
export type GapRule = {
  state: 'waiting' | 'pending' | 'open' | 'closed' | 'skipped' | 'cancelled' | 'none'
  stop: number | null
  /** 只有 C：昨收 */
  target: number | null
  signal_time: string | null
  entry_price: number | null
  entry_time: string | null
}

export type GapFail = {
  version: 1
  names: Record<GapSleeve, string>
  /** 開高門檻（%），跳空研究探索期 80 分位 */
  threshold_pct: number
  /** 開盤跳空（%）；還沒開盤 → null */
  gap_pct: number | null
  /** 今天是不是開高日；還沒開盤 → null */
  active: boolean | null
  /** B 的開盤區間（08:45～08:59），09:00 才有 */
  range: { high: number; low: number } | null
  rules: Record<GapSleeve, GapRule>
  sleeves: Record<GapSleeve, SleeveState>
  trades: FuturesTrade[]
  events: FuturesEvent[]
  total: number
}

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
  /** 大台、微台的模擬單才有（T-451）；小台沒有就是 MTX、每點 50 元、手續費 30 元 */
  product?: Product
  name?: string
  volume?: number
  point_value?: number
  commission?: number
  /** 開高走低（T-452）：2026-10-27 之後上傳的才有 */
  gap_fail?: GapFail
}

/** gap_total：開高走低的合計（那天的資料沒有開高走低 → null） */
export type FuturesDayRow = { day: string; source: string; status: string; total: number | null; match: boolean | null; gap_total?: number | null }

/** 大台、微台行情（T-451，/api/futures/quotes）：只有行情，沒有模擬單。欄位同 fubon_live.py 的 QuoteTracker.snapshot。 */
export type QuoteProduct = 'TX' | 'TMF'
export type QuoteDay = {
  version: 1
  day: string
  product: QuoteProduct
  name: string
  source: 'fubon_live' | 'taifex_replay'
  status: 'live' | 'closed'
  contract: string
  symbol: string
  updated_at: string
  last_tick: string | null
  last_price: number | null
  prev_close: number | null
  open: number | null
  volume: number
  bars: [string, number, number, number, number, number][]
}
export type QuoteDayRow = { day: string; source: string; status: string; total?: number | null; match?: boolean | null; gap_total?: number | null }

/** 大台／微台那一天：前面夠 14 天就有模擬單（同小台的整份狀態），不夠就只有行情。 */
export type QuotePayload = QuoteDay | (FuturesDay & { product: QuoteProduct; name: string; volume: number })
export const isPaper = (d: QuotePayload | FuturesDay): d is FuturesDay => 'sleeves' in d

/** 網站上的三個商品：小台（模擬單）、大台、微台。 */
export type Product = 'MTX' | QuoteProduct
export const PRODUCT_NAME: Record<Product, string> = { MTX: '小台', TX: '大台', TMF: '微台' }

/** 秒級復盤資料（/api/futures/seconds）：08:45:00 起有成交的每一秒；s／p／w 都是差分整數，w 是 VWAP×10。 */
export type SecondsPayload = { version: 1; day: string; t0: string; s: number[]; p: number[]; w: number[] }

/** 解開後：每一點的盤中時間 HH:MM:SS、最後成交價、VWAP。 */
export type Seconds = { label: string[]; price: number[]; vwap: number[] }

/** 子帳戶的白話名稱（地鐵告示：名字短、顏色分類）。 */
export const SLEEVE_NAME: Record<Sleeve, string> = {
  ORB_LONG: '開盤突破・只做多',
  NOISE_SHORT: '噪音區・只放空',
  GAP_A: 'A 開高失守',
  GAP_B: 'B 跌破開盤區間',
  GAP_C: 'C 補缺口',
}

/** 圖上買賣點前面的代號：開高走低三條同時進場時看得出是哪幾條。 */
export const SLEEVE_TAG: Record<Sleeve, string> = { ORB_LONG: '', NOISE_SHORT: '', GAP_A: 'A', GAP_B: 'B', GAP_C: 'C' }

/** 出場原因（同 fubon_live.py 的 REASON_TEXT）。 */
export const REASON_TEXT: Record<string, string> = {
  STOP: '停損',
  TARGET: '停利',
  EOD: '收盤前',
  EXIT: '回到下緣之上',
  FLIP: '反手',
}

// 「現在平倉的話」的估算：與 intraday_bidir_test.py 相同（每邊滑價 1 點、期交稅十萬分之二）；
// 每點幾元、手續費照商品（小台 50 元／30 元；大台、微台看那一天的資料，fubon_live.py 的 PAPER_COST）
const SLIP = 1
const TAX = 0.00002
export type Cost = { point_value: number; commission: number }
export const MTX_COST: Cost = { point_value: 50, commission: 30 }

/** 那一天的成本（資料裡沒寫＝小台）。 */
export const costOf = (d: { point_value?: number; commission?: number }): Cost => ({
  point_value: d.point_value ?? MTX_COST.point_value,
  commission: d.commission ?? MTX_COST.commission,
})

/** 現在用 price 平倉的淨損益（含出場滑價、來回手續費與期交稅）。 */
export function closeNowPnl(side: 1 | -1, entry: number, price: number, cost: Cost = MTX_COST): number {
  const exit = price - side * SLIP
  return side * (exit - entry) * cost.point_value - 2 * cost.commission - TAX * (entry + exit) * cost.point_value
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

/** 買賣點（成交、平倉）的時間，照先後；復盤的「上一個／下一個買賣點」用（給那一天或某個策略的帳本）。 */
export const fillTimes = (d: { events: FuturesEvent[] }): string[] =>
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

export type Fill<S extends Sleeve = Sleeve> = { time: string; sleeve: S; side: 1 | -1; price: number; kind: 'entry' | 'exit' }

export type ReplayView<S extends Sleeve = ComboSleeve> = {
  cut: string
  bars: FuturesDay['bars']
  events: FuturesEvent[]
  price: number | null
  /** 每個子帳戶在 cut 那一刻：部位、進場價、已實現、現在平倉的話 */
  sleeves: Record<S, SleeveState>
  total: number
  /** 已經發生的成交（圖上的標記） */
  fills: Fill<S>[]
}

/** 一個策略的帳本：有哪些子帳戶、成交、最新部位、時間軸。 */
export type Book<S extends Sleeve> = { sleeves: readonly S[]; trades: FuturesTrade[]; states: Record<S, SleeveState>; events: FuturesEvent[] }

export const comboBook = (d: FuturesDay): Book<ComboSleeve> => ({ sleeves: SLEEVES, trades: d.trades, states: d.sleeves, events: d.events })

/** 開高走低的帳本；那一天的資料沒有（10-27 之前上傳的）→ null。 */
export const gapBook = (d: FuturesDay): Book<GapSleeve> | null =>
  d.gap_fail ? { sleeves: GAP_SLEEVES, trades: d.gap_fail.trades, states: d.gap_fail.sleeves, events: d.gap_fail.events } : null

/**
 * 復盤：拉到 cut（HH:MM 或 HH:MM:SS）那一刻看得到什麼。只用 cut 之前的 K 棒、事件與成交，沒有偷看後面。
 * 秒級時：價格用那一秒以前最後一筆；那一分鐘還沒走完的 K 棒不算（它含了 cut 之後的成交）。
 * 還在盤中的那一天，最後一筆還沒出場的部位在 trades 裡沒有，從 states 補上。
 */
export function replayBook<S extends Sleeve>(d: FuturesDay, book: Book<S>, cut: string, secs: Seconds | null = null): ReplayView<S> {
  const { bars, price } = barsAt(d.bars, cut, secs)
  const fills: Fill<S>[] = []
  const sleeves = {} as Record<S, SleeveState>
  for (const s of book.sleeves) {
    let realized = 0
    let open: { side: 1 | -1; entry: number } | null = null
    for (const t of book.trades.filter((x) => x.sleeve === s)) {
      if (within(t.entry_time, cut)) fills.push({ time: t.entry_time, sleeve: s, side: t.side, price: t.entry_price, kind: 'entry' })
      if (within(t.exit_time, cut)) {
        realized += t.net_pnl
        fills.push({ time: t.exit_time, sleeve: s, side: t.side, price: t.exit_price, kind: 'exit' })
      } else if (within(t.entry_time, cut)) open = { side: t.side, entry: t.entry_price }
    }
    const live = book.states[s]
    if (open === null && live.position !== 0 && live.entry_price !== null) {
      const entered = [...book.events].reverse().find((e) => e.sleeve === s && e.kind === 'fill')
      if (entered && within(entered.time, cut)) {
        open = { side: live.position as 1 | -1, entry: live.entry_price }
        fills.push({ time: entered.time, sleeve: s, side: open.side, price: open.entry, kind: 'entry' })
      }
    }
    const openPnl = open && price !== null ? Math.round(closeNowPnl(open.side, open.entry, price, costOf(d))) : null
    sleeves[s] = { position: open ? open.side : 0, entry_price: open ? open.entry : null, realized: Math.round(realized), open_pnl: openPnl }
  }
  const total = book.sleeves.reduce((a, s) => a + sleeves[s].realized + (sleeves[s].open_pnl ?? 0), 0)
  fills.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
  return { cut, bars, events: book.events.filter((e) => within(e.time, cut)), price, sleeves, total, fills }
}

/** 固定組合的復盤（T-450）。 */
export const replayAt = (d: FuturesDay, cut: string, secs: Seconds | null = null): ReplayView<ComboSleeve> =>
  replayBook(d, comboBook(d), cut, secs)

/** 圖上的買賣點：同一刻、同價、同方向的合成一個（開高走低 A、B、C 常常同時進場），文字標出是哪幾條。 */
export function chartMarks(fills: readonly Fill[], secs: boolean): { time: string; side: 1 | -1; price: number; kind: 'entry' | 'exit'; text: string }[] {
  const out: { time: string; side: 1 | -1; price: number; kind: 'entry' | 'exit'; tags: string[] }[] = []
  for (const f of fills) {
    const time = secs ? f.time.slice(0, 8) : f.time.slice(0, 5)
    const same = out.find((m) => m.time === time && m.price === f.price && m.kind === f.kind && m.side === f.side)
    const tag = SLEEVE_TAG[f.sleeve]
    if (same) {
      if (tag) same.tags.push(tag)
    } else out.push({ time, side: f.side, price: f.price, kind: f.kind, tags: tag ? [tag] : [] })
  }
  return out.map(({ tags, ...m }) => {
    const word = m.kind === 'entry' ? (m.side > 0 ? '買' : '空') : m.side > 0 ? '賣' : '補'
    return { ...m, text: `${tags.length ? `${tags.join('')} ` : ''}${word} ${m.price.toFixed(0)}` }
  })
}

/**
 * 開高走低每一條在 cut 那一刻的白話狀態（數字卡下面那行）。只用 cut 之前就知道的事：
 * 開盤就知道有沒有開高；B 的開盤區間 09:00 才有；訊號、取消都比到 cut。
 */
export function gapSub(d: FuturesDay, g: GapFail, s: GapSleeve, st: SleeveState, cut: string): string {
  if (st.position !== 0) return `持空 1 口 @ ${st.entry_price?.toFixed(0) ?? '—'}（現在平倉 ${yuan(st.open_pnl ?? 0)}）`
  if (g.active === null || d.open === null) return '還沒開盤'
  if (!g.active) return `沒有開高（${signedPct(g.gap_pct)}），今天不做`
  const r = g.rules[s]
  if (r.entry_time !== null && within(r.entry_time, cut)) return st.realized ? `已平倉，這邊 ${yuan(st.realized)}` : '已平倉'
  if (r.signal_time !== null && within(r.signal_time, cut)) {
    if (r.state === 'skipped') return '缺口已經補完，不做'
    if (r.state === 'cancelled') return '訊號取消'
    return '訊號出現，等成交'
  }
  if (r.state === 'none' && cut >= '13:44') return '今天沒等到訊號'
  if (s === 'GAP_B') return cut < '09:00' || g.range === null ? '09:00 定開盤區間' : `等跌破 ${g.range.low.toFixed(0)}`
  return `等跌破開盤 ${d.open.toFixed(0)}`
}

/** 跳空百分比的寫法：+0.56%／-0.12%。 */
export const signedPct = (v: number | null): string => (v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

/** 開高走低圖上的水平線（只畫 cut 那一刻已經知道的）：開盤（A、C 的觸發）、昨收（C 的停利）、09:00 後的區間低點（B 的觸發）、持倉的停損。 */
export function gapLevels(d: FuturesDay, g: GapFail, view: ReplayView<GapSleeve>): { price: number; title: string }[] {
  if (!g.active || d.open === null) return []
  const out = [
    { price: d.open, title: '開盤' },
    { price: d.prev_close, title: '昨收' },
  ]
  if (g.range !== null && view.cut >= '09:00') out.push({ price: g.range.low, title: 'B 區間低' })
  const stops = new Map<number, string[]>()
  for (const s of GAP_SLEEVES) {
    const stop = g.rules[s].stop
    if (view.sleeves[s].position !== 0 && stop !== null) stops.set(stop, [...(stops.get(stop) ?? []), SLEEVE_TAG[s]])
  }
  for (const [price, tags] of stops) out.push({ price, title: `${tags.join('')} 停損` })
  return out
}

/**
 * 拉到 cut 那一刻看得到的 K 棒與價格（小台、大台、微台共用）：
 * 秒級時價格用那一秒以前最後一筆，那一分鐘還沒走完的 K 棒不算；1 分 K 時含 cut 那一分鐘。
 */
export function barsAt(all: FuturesDay['bars'], cut: string, secs: Seconds | null = null): { bars: FuturesDay['bars']; price: number | null } {
  const bars = all.filter((b) => (cut.length > 5 ? b[0] < cut.slice(0, 5) : b[0] <= cut))
  let price = bars.length ? bars[bars.length - 1][4] : null
  if (secs !== null) {
    const i = lastAtOrBefore(secs.label, cut)
    price = i >= 0 ? secs.price[i] : null
  }
  return { bars, price }
}

/** 漲跌：點數與百分比（昨收沒有 → null）。 */
export function change(price: number | null, prevClose: number | null): { pts: number; pct: number } | null {
  if (price === null || prevClose === null || prevClose === 0) return null
  return { pts: price - prevClose, pct: (price - prevClose) / prevClose }
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
