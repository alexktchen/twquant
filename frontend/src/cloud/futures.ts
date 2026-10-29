// 小台模擬（T-450／D-220）：資料型別與純函式（盤中、復盤共用；node 單元測試 tests/cloud-futures.test.ts）。
// 資料由使用者電腦上的 research/momentum_scalping/scripts/fubon_live.py 寫進 serving.futures_paper；
// 欄位改了要兩邊一起改（那支程式的 LiveCombo.snapshot）。規則 2026-10-26 凍結：ORB 只做多 + 噪音區只放空，各 1 口小台。
// T-456：「網友原版」（seromitama/Momentum_Scalping 原封不動，只做多 1 口，studies/ms_tmf）在 ms（Netizen.snapshot）；T-460 起是主角。
// T-461（使用者 2026-10-29：「開高走低策略直接拿掉」）：開高走低（T-452）拿掉；它和固定組合合成的「帳戶」（T-454）只剩固定組合，一起收掉。
// 舊的日子 payload 裡還有 gap_fail，畫面不讀。

/** 兩個策略：網友原版（主，只做多）、固定組合（輔助：ORB 只做多 + 噪音區只放空）。 */
export type Strategy = 'combo' | 'ms'
export const STRATEGY_NAME: Record<Strategy, string> = { combo: '固定組合', ms: '網友原版' }

export type ComboSleeve = 'ORB_LONG' | 'NOISE_SHORT'
export type MsSleeve = 'MS_LONG'
export type Sleeve = ComboSleeve | MsSleeve
export const SLEEVES: readonly ComboSleeve[] = ['ORB_LONG', 'NOISE_SHORT']
export const MS_SLEEVES: readonly MsSleeve[] = ['MS_LONG']

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
  /** 只有網友原版：進場時的停損、進場型態（動能・初次突破…） */
  stop_price?: number
  setup?: string
}

export type SleeveState = { position: -1 | 0 | 1; entry_price: number | null; realized: number; open_pnl: number | null }

export type OrbBox = { open: number; close: number; high: number; low: number; side: -1 | 0 | 1 }

/** 網友原版（同 fubon_live.py 的 Netizen.snapshot）：只做多 1 口；official 是收盤用期交所格式逐筆重算的對照。 */
export type Netizen = {
  version: 1
  name: string
  commit: string
  sleeves: Record<MsSleeve, SleeveState>
  /** 持倉中的那一筆；空手 → null */
  open: { entry_time: string; entry_price: number; stop: number; setup: string } | null
  trades: FuturesTrade[]
  events: FuturesEvent[]
  /** 通過送單前驗證的訊號數 */
  signals: number
  total: number
  official: FuturesTrade[] | null
  official_match: boolean | null
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
  /** 網友原版（T-456）：2026-10-28 晚上之後上傳的才有 */
  ms?: Netizen | null
}

/** total＝固定組合的合計、ms_total＝網友原版的合計（那天的資料沒有網友原版 → null）。 */
export type FuturesDayRow = { day: string; source: string; status: string; total: number | null; match: boolean | null; ms_total?: number | null; ms_est?: boolean }

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
export type QuoteDayRow = { day: string; source: string; status: string; total?: number | null; match?: boolean | null; ms_total?: number | null; ms_est?: boolean }

/** 網友原版的 1 分 K 估算（T-462，/api/futures/estimate）：沒有逐筆的日子才有；偏悲觀（每筆少算約 10 點）。 */
export type MsEstimateTrade = {
  entry_time: string
  entry_price: number
  exit_time: string
  exit_price: number
  exit_reason: string
  points: number
  net_pnl: number
  setup: string
}
export type MsEstimate = { day: string; product: string; source: string; total: number; n: number; trades: MsEstimateTrade[] }

/** 日曆上這天是不是只有網友原版的 1 分 K 估算（沒有逐筆模擬單，點進去看估算，不是秒級復盤）。 */
export const isEstimate = (r: { ms_est?: boolean } | undefined | null): boolean => r?.ms_est === true

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
  MS_LONG: '網友原版・只做多',
}

/** 圖上買賣點前面的代號：兩個策略畫在同一張圖，看得出是哪一條。 */
export const SLEEVE_TAG: Record<Sleeve, string> = { ORB_LONG: '突破', NOISE_SHORT: '噪音', MS_LONG: '網友' }

/** 出場原因（同 fubon_live.py 的 REASON_TEXT）。 */
export const REASON_TEXT: Record<string, string> = {
  STOP: '停損',
  TARGET: '停利',
  EOD: '收盤前',
  EXIT: '回到下緣之上',
  FLIP: '反手',
  // 網友原版的出場（同 fubon_live.py 的 MS_EXIT_TEXT）
  EMA_TREND_BREAK: 'EMA 死叉',
  HIGHER_LOW_BREAK: '跌破前低',
  MOMENTUM_STRUCTURE_BREAK_FULL: '動能結構破壞',
  MOMENTUM_DECAY_PARTIAL: '動能衰退減碼',
  EMERGENCY_STOP: '2.5% 緊急停損',
  TIME_EXIT: '13:00 清倉',
  MARKET_TREND_INVALIDATED: '大盤轉弱',
  RS_FAILURE: '強度轉弱',
  UNKNOWN_STRATEGY_STOP: '停損',
  EOD_FORCED_CLOSE: '收盤前',
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

/** 網友原版的帳本（T-456）；那一天的資料沒有 → null。 */
export const msBook = (d: FuturesDay): Book<MsSleeve> | null =>
  d.ms ? { sleeves: MS_SLEEVES, trades: d.ms.trades, states: d.ms.sleeves, events: d.ms.events } : null

/** 網友原版在 cut 那一刻持有的那一筆的停損（圖上的水平線，只畫那一刻真的持有的）；空手 → null。 */
export function msStopAt(ms: Netizen, view: { cut: string; sleeves: Record<MsSleeve, SleeveState> }): number | null {
  if (view.sleeves.MS_LONG.position === 0) return null
  const held = ms.trades.find((t) => within(t.entry_time, view.cut) && !within(t.exit_time, view.cut))
  if (held) return held.stop_price ?? null
  return ms.open && within(ms.open.entry_time, view.cut) ? ms.open.stop : null
}

/** 網友原版在那一刻的數字卡（T-460：台指期以網友原版為主）：持倉那一格、平倉幾筆、已實現。 */
export function msKpis(v: ReplayView<MsSleeve>): { hold: { value: string; text: string; tone: number | null }; exits: number; realized: number } {
  const st = v.sleeves.MS_LONG
  const exits = v.fills.filter((f) => f.kind === 'exit').length
  const hold =
    st.position !== 0
      ? { value: yuan(st.open_pnl ?? 0), text: `持多 1 口 @ ${st.entry_price?.toFixed(0) ?? '—'}・現在平倉的話`, tone: st.open_pnl }
      : { value: '空手', text: v.cut >= '13:00' ? '13:00 後不再進場' : '等動能或趨勢訊號', tone: null }
  return { hold, exits, realized: st.realized }
}

/** 網友原版在那一刻的一句話（各策略那一列）：持多、今天平倉幾筆、還在等訊號。 */
export function msSub(v: ReplayView<MsSleeve>): string {
  const st = v.sleeves.MS_LONG
  if (st.position !== 0) return `持多 1 口 @ ${st.entry_price?.toFixed(0) ?? '—'}（現在平倉 ${yuan(st.open_pnl ?? 0)}）`
  const exits = v.fills.filter((f) => f.kind === 'exit').length
  if (exits) return `已平倉 ${exits} 筆，這邊 ${yuan(st.realized)}`
  return v.cut >= '13:00' ? '今天沒有進場' : '等動能或趨勢訊號'
}

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
export function chartMarks(
  fills: readonly Fill[],
  secs: boolean,
  primary: Sleeve | null = null,
): { time: string; side: 1 | -1; price: number; kind: 'entry' | 'exit'; text: string; aux: boolean }[] {
  const out: { time: string; side: 1 | -1; price: number; kind: 'entry' | 'exit'; tags: string[]; aux: boolean }[] = []
  for (const f of fills) {
    const time = secs ? f.time.slice(0, 8) : f.time.slice(0, 5)
    const same = out.find((m) => m.time === time && m.price === f.price && m.kind === f.kind && m.side === f.side)
    const tag = SLEEVE_TAG[f.sleeve]
    // T-460：有主策略（網友原版）時，其他策略的買賣點是輔助（圖上畫灰色）；同一點合併到主策略就不算輔助
    const aux = primary !== null && f.sleeve !== primary
    if (same) {
      if (tag) same.tags.push(tag)
      same.aux = same.aux && aux
    } else out.push({ time, side: f.side, price: f.price, kind: f.kind, tags: tag ? [tag] : [], aux })
  }
  return out.map(({ tags, ...m }) => {
    const word = m.kind === 'entry' ? (m.side > 0 ? '買' : '空') : m.side > 0 ? '賣' : '補'
    const label = tags.every((t) => t.length === 1) ? tags.join('') : tags.join('・')
    return { ...m, text: `${label ? `${label} ` : ''}${word} ${m.price.toFixed(0)}` }
  })
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

// ── T-453：各策略合在同一個畫面、每日損益日曆（使用者 2026-10-27：「都整合在一起…再呈現各策略賺賠…用成日曆一次可以看完當天賺多少」） ──

/** 時間軸上的事件標上屬於哪個策略。 */
export type TaggedEvent = FuturesEvent & { strategy: Strategy }

const byTime = <T extends { time: string }>(a: T, b: T) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)

/** 兩個策略的事件照時間排在一起；同一秒固定組合排前面。 */
export function mergeEvents(combo: readonly FuturesEvent[], ms: readonly FuturesEvent[] = []): TaggedEvent[] {
  return [...combo.map((e) => ({ ...e, strategy: 'combo' as const })), ...ms.map((e) => ({ ...e, strategy: 'ms' as const }))].sort(byTime)
}

/** 每個子帳戶是多還是空。 */
export const SIDE_OF: Record<Sleeve, 1 | -1> = { ORB_LONG: 1, NOISE_SHORT: -1, MS_LONG: 1 }

/** 一個策略多或空那一邊在那一刻：損益、一句話（持有哪一條的 1 口、平倉幾筆、空手）。沒有網友原版的舊日子，數字卡用固定組合的這個。 */
export function sideSummary<S extends Sleeve>(v: ReplayView<S>, side: 1 | -1): { value: number; text: string } {
  const mine = (Object.keys(v.sleeves) as S[]).filter((x) => SIDE_OF[x] === side)
  const value = mine.reduce((a, x) => a + v.sleeves[x].realized + (v.sleeves[x].open_pnl ?? 0), 0)
  const held = mine.find((x) => v.sleeves[x].position !== 0)
  if (held) return { value, text: `${side > 0 ? '持多' : '持空'} 1 口・${SLEEVE_NAME[held]} @ ${v.sleeves[held].entry_price?.toFixed(0) ?? '—'}` }
  const exits = v.fills.filter((f) => f.kind === 'exit' && SIDE_OF[f.sleeve] === side).length
  return { value, text: exits ? `平倉 ${exits} 筆` : '空手' }
}

export type DayView = {
  cut: string
  bars: FuturesDay['bars']
  price: number | null
  /** 固定組合（輔助）：兩條各做 1 口 */
  combo: ReplayView<ComboSleeve>
  /** 網友原版（主）；那一天的資料沒有 → null */
  ms: ReplayView<MsSleeve> | null
  /** 圖上的買賣點：兩個策略都有（畫面把網友原版以外的畫成灰色） */
  fills: Fill[]
  events: TaggedEvent[]
}

/** 拉到 cut 那一刻：網友原版與固定組合各自的帳（規則同 replayBook，不劇透）。 */
export function dayAt(d: FuturesDay, cut: string, secs: Seconds | null = null): DayView {
  const combo = replayAt(d, cut, secs)
  const mb = msBook(d)
  const ms = mb ? replayBook(d, mb, cut, secs) : null
  const events = mergeEvents(combo.events, ms?.events ?? [])
  const fills: Fill[] = [...combo.fills, ...(ms?.fills ?? [])].sort(byTime)
  return { cut, bars: combo.bars, price: combo.price, combo, ms, fills, events }
}

/** 兩個策略所有買賣點的時間（「上一個／下一個買賣點」）。 */
export const allFillTimes = (d: FuturesDay): string[] => fillTimes({ events: [...d.events, ...(d.ms?.events ?? [])] }).sort()

/** 固定組合在那一刻的一句話：持多／持空、平倉幾筆、空手。 */
export function comboSummary(v: ReplayView<ComboSleeve>): string {
  const holding = SLEEVES.filter((s) => v.sleeves[s].position !== 0).map((s) => (v.sleeves[s].position > 0 ? '持多 1 口' : '持空 1 口'))
  if (holding.length) return holding.join('、')
  const exits = v.fills.filter((f) => f.kind === 'exit').length
  return exits ? `平倉 ${exits} 筆` : '空手'
}

/** 日曆上看哪一個：網友原版（主，預設）、固定組合（輔助）。 */
export type CalView = Strategy
export const CAL_VIEWS: readonly CalView[] = ['ms', 'combo']
export const CAL_VIEW_NAME: Record<CalView, string> = { ms: '網友原版', combo: '固定組合' }

/** 日期清單的一列（/api/futures/days、/quotes/days）：total＝固定組合、ms_total＝網友原版。 */
export type PnlRow = {
  day: string
  /** T-462：這天只有網友原版的 1 分 K 估算 */
  ms_est?: boolean
  total?: number | null
  /** 網友原版的合計（T-456；那天的資料沒有 → null） */
  ms_total?: number | null
}

/** 那一天的損益：網友原版或固定組合；沒有資料 → null。 */
export function dayPnl(r: PnlRow, view: CalView): number | null {
  return view === 'combo' ? (r.total ?? null) : (r.ms_total ?? null)
}

/** 一個月的週一到週五格子：一週一列、五格；不是這個月的格子是 null。月份寫 YYYY-MM。 */
export function monthWeeks(month: string): (string | null)[][] {
  const [y, m] = month.split('-').map(Number)
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate()
  const weeks: (string | null)[][] = []
  let row: (string | null)[] | null = null
  for (let d = 1; d <= last; d++) {
    const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
    if (wd === 0 || wd === 6) continue
    if (row === null || wd === 1) {
      row = [null, null, null, null, null]
      weeks.push(row)
    }
    row[wd - 1] = `${month}-${pad(d)}`
  }
  return weeks
}

/** 有資料的月份（舊到新）。 */
export const monthsOf = (rows: readonly PnlRow[]): string[] => [...new Set(rows.map((r) => r.day.slice(0, 7)))].sort()

/** 一個月的小結：合計、賺幾天、賠幾天、有損益的天數（只有行情的日子不算）。 */
export function monthSummary(rows: readonly PnlRow[], month: string, view: CalView): { total: number; days: number; win: number; loss: number } {
  const vals = rows
    .filter((r) => r.day.startsWith(month))
    .map((r) => dayPnl(r, view))
    .filter((v): v is number => v !== null)
    .map((v) => Math.round(v))
  return { total: vals.reduce((a, v) => a + v, 0), days: vals.length, win: vals.filter((v) => v > 0).length, loss: vals.filter((v) => v < 0).length }
}

/** 日曆格子裡的金額：一萬以上寫「萬」（+4.7萬），以下照寫（-3,278）。 */
export function compactYuan(v: number): string {
  const r = Math.round(v)
  if (Math.abs(r) >= 10_000) return `${r > 0 ? '+' : '-'}${(Math.abs(r) / 10_000).toFixed(1)}萬`
  return yuan(r)
}

// ── T-455：盤口與大單（使用者 2026-10-28：「可以讓我在前端介面看到錄製的資料嗎」「做成可切換的」） ──
// 資料是 fubon_live.py 的 Flow.snapshot（payload 的 flow），/api/futures/flow 給；只有富邦即時錄到、2026-10-28 之後的日子才有。

/** 一筆大單：[HH:MM:SS, 價, 口, 主動方向（+1 主動買、−1 主動賣、0 不明）] */
export type BigTrade = [string, number, number, -1 | 0 | 1]
/** 五檔的一檔：[價, 口] */
export type BookLevel = [number, number]
export type OrderBook = { time: string; bids: BookLevel[]; asks: BookLevel[] }
export type Flow = {
  version: 1
  day: string
  product: Product
  /** 伺服器已經照這個門檻篩過大單 */
  big_min: number
  /** 每分鐘：[HH:MM, 主動買口數, 主動賣口數, 不明口數] */
  minutes: [string, number, number, number][]
  buy: number
  sell: number
  big: BigTrade[]
  /** 最新五檔（盤中用） */
  book: OrderBook | null
  /** 每分鐘最後一份五檔：[HH:MM, bids, asks]（復盤用；盤中 live=1 不給） */
  books?: [string, BookLevel[], BookLevel[]][] | null
}

/** 大單門檻可以切的幾個（一筆幾口以上）；錄製程式記 5 口以上，所以最低 5。 */
export const BIG_THRESHOLDS = [5, 10, 20, 30] as const
export type BigMin = (typeof BIG_THRESHOLDS)[number]
/** 預設門檻：照各商品一筆成交的大小分布（2026-10-28 開盤 6 分鐘：小台、大台 ≥10 口約前 1%，微台 ≥20 口約前 1%） */
export const DEFAULT_BIG_MIN: Record<Product, BigMin> = { MTX: 10, TX: 10, TMF: 20 }

export type FlowView = {
  cut: string
  /** 到 cut 為止的主動買、主動賣口數與差 */
  buy: number
  sell: number
  delta: number
  /** 每分鐘淨主動（買 − 賣），到 cut；秒級時還沒走完的那一分鐘不算（同 barsAt） */
  minutes: [string, number][]
  /** 到 cut 為止、≥ min 口的大單，照時間 */
  big: BigTrade[]
  /** 那一刻看得到的五檔：盤中是最新一份；復盤用那一分鐘之前最後一份；沒有 → null */
  book: OrderBook | null
}

/** 拉到 cut 那一刻的盤口與大單（不偷看後面）；min＝大單門檻。 */
export function flowAt(f: Flow, cut: string, min: number): FlowView {
  const minutes = f.minutes.filter((m) => (cut.length > 5 ? m[0] < cut.slice(0, 5) : m[0] <= cut))
  const buy = minutes.reduce((a, m) => a + m[1], 0)
  const sell = minutes.reduce((a, m) => a + m[2], 0)
  const big = f.big.filter((b) => b[2] >= min && within(b[0], cut))
  let book: OrderBook | null = null
  if (f.books && f.books.length) {
    const seen = f.books.filter((b) => (cut.length > 5 ? b[0] < cut.slice(0, 5) : b[0] <= cut))
    const last = seen[seen.length - 1]
    if (last) book = { time: last[0], bids: last[1], asks: last[2] }
  } else if (cut >= '23:59' && f.book) book = f.book
  return { cut, buy, sell, delta: buy - sell, minutes: minutes.map((m) => [m[0], m[1] - m[2]]), big, book }
}

/** 五檔合計：買盤幾口、賣盤幾口、買盤佔比（0～1）；空的 → null。 */
export function bookTotals(b: OrderBook | null): { bid: number; ask: number; bidShare: number } | null {
  if (!b || (!b.bids.length && !b.asks.length)) return null
  const bid = b.bids.reduce((a, l) => a + l[1], 0)
  const ask = b.asks.reduce((a, l) => a + l[1], 0)
  return { bid, ask, bidShare: bid + ask ? bid / (bid + ask) : 0.5 }
}

/** 口數寫法：1,234 口。 */
export const lots = (v: number): string => `${Math.round(v).toLocaleString('en-US')} 口`
