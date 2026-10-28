// 個股當沖（T-458／D-221）：資料型別與純函式（盤中、復盤共用；node 單元測試 tests/cloud-daytrade.test.ts）。
// 資料由使用者電腦上的 research/momentum_scalping/scripts/stock_live.py 寫進 serving.stock_paper（整份狀態）與 stock_paper_history（復盤）；
// 欄位改了要兩邊一起改（那支程式的 Recorder.payload／history）。
// 選股與進出場是網友原版 Momentum_Scalping（seromitama，commit 1dea51b）原封不動：每 30 秒掃一次全市場、前 30 檔才看逐筆，只做多、只記帳。

/** 強勢股榜的一列（網友原版 Scanner 的輸出；欄位可能少，畫面要容忍 null）。 */
export type BoardRow = {
  rank: number | null
  symbol: string
  name: string
  market: 'TWSE' | 'TPEX'
  sector?: string | null
  /** 總分（0～100，70 分以上才進榜） */
  score: number | null
  /** 動能分數 */
  momentum?: number | null
  change_pct: number | null
  volume?: number | null
  turnover?: number | null
  volume_ratio?: number | null
  /** 比大盤強（百分點） */
  market_relative_strength?: number | null
  sector_relative_strength?: number | null
  /** 離當日高點（%） */
  distance_to_intraday_high_pct?: number | null
  day_trade_eligible?: boolean | null
  trade_ready?: boolean | null
  trade_block_reasons?: string[] | null
}

export type Position = {
  symbol: string
  market: string
  name: string
  strategy: string | null
  entry_time: string | null
  entry_price: number
  /** 張 */
  quantity: number
  stop: number
  last: number | null
  /** 現在賣掉的話（扣手續費、證交稅、吃一檔） */
  open_pnl: number | null
}

export type DayTrade = {
  symbol: string
  name: string
  market: string
  strategy: string
  setup: string
  entry_time: string
  entry_price: number
  quantity: number
  stop_price: number
  exit_time: string
  exit_price: number
  exit_reason: string
  net_pnl: number
  net_ret_pct: number
  fees: number
  tax: number
  hold_minutes: number
}

export type DayEvent = { time: string; kind: 'signal' | 'fill' | 'exit' | 'info'; symbol: string | null; name: string; text: string }

export type MarketView = {
  state: string | null
  trend: string | null
  score: number | null
  taiex_pct: number | null
  tpex_pct: number | null
  breadth: number | null
  momentum_allowed: boolean | null
  trend_allowed: boolean | null
}

export type DayTradeDay = {
  version: 1
  day: string
  status: 'live' | 'closed'
  source: 'fubon_live' | 'minute_replay'
  updated_at: string
  board_time: string | null
  market: MarketView
  board: BoardRow[]
  positions: Position[]
  trades: DayTrade[]
  events: DayEvent[]
  events_total: number
  account: { capital: number; realized: number; open_pnl: number; total: number; entries: number; closed: number }
  config: { capital: number; watch_count: number; fee_discount: number; min_fee: number; tax_rate: number; latency_ms: number; trading: Record<string, number> }
  watch: { in: number; out: number }
}

export type DayTradeRow = { day: string; source: string; status: string; total: number | null; closed: number | null }

/** 復盤資料：榜單每 30 秒一份（[時間, [[代號, 漲幅%, 分數], …]]，照名次）、有成交的股票的 1 分 K 與秒級。 */
export type DayTradeHistory = {
  version: 1
  day: string
  names: Record<string, string>
  board_history: [string, [string, number | null, number | null][]][]
  charts: Record<string, { bars: [string, number, number, number, number, number][]; secs: { t0: string; s: number[]; p: number[] } | null }>
}

/** 大盤狀態的白話（原版 market 模組的狀態名）。 */
export const MARKET_STATE_TEXT: Record<string, string> = { BULL: '多頭', NORMAL: '正常', WEAK: '偏弱', PANIC: '恐慌', UNKNOWN: '不明' }

/** 原版的進場型態、出場原因（同 stock_live.py 的 SETUP_TEXT、stock_engine.py 的 EXIT_TEXT）。 */
export const EXIT_TEXT: Record<string, string> = {
  EMA_TREND_BREAK: 'EMA 死叉',
  HIGHER_LOW_BREAK: '跌破前低',
  MOMENTUM_STRUCTURE_BREAK_FULL: '動能結構破壞',
  MOMENTUM_DECAY_PARTIAL: '動能衰退減碼',
  EMERGENCY_STOP: '緊急停損',
  TIME_EXIT: '13:00 清倉',
  CLOCK_TIME_EXIT: '13:00 清倉',
  MARKET_TREND_INVALIDATED: '大盤轉弱',
  RS_FAILURE: '強度轉弱',
  UNKNOWN_STRATEGY_STOP: '停損',
  EOD_FORCED_CLOSE: '收盤強制了結',
}

/** 帳戶設定的白話名稱（stock_live.py 的 config_view；網友預設，可在電腦上的 account.json 改）。 */
export const CONFIG_TEXT: Record<string, string> = {
  max_position_count: '同時最多幾檔',
  max_single_position_amount: '單檔最多幾元',
  max_single_position_pct: '單檔最多佔資金 %',
  max_risk_per_trade_pct: '每筆最多賠資金 %',
  max_buy_price: '股價上限（元）',
  max_entry_day_change_pct: '漲幅超過幾 % 不追',
  max_daily_trade_count: '一天最多幾筆',
  max_daily_loss_pct: '當日虧幾 % 停手',
  max_entries_per_symbol_per_day: '同一檔一天最多進場幾次',
  reentry_cooldown_minutes: '出場後幾分鐘才能再進',
  max_consecutive_losses: '連虧幾筆就休息',
  loss_cooldown_minutes: '連虧後休息幾分鐘',
  max_stop_distance_pct: '停損最遠幾 %',
  emergency_stop_loss_pct: '緊急停損 %',
  max_sector_position_count: '同一類股最多幾檔',
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

/** 盤中時間有沒有在 cut 之前（含）：比到 cut 的精度。 */
const within = (time: string, cut: string): boolean => time.slice(0, cut.length) <= cut

/** 那一刻的榜單（最後一份 ≤ cut）；開盤前 → 空。 */
export function boardAt(h: DayTradeHistory, cut: string): { time: string | null; rows: { rank: number; symbol: string; name: string; change_pct: number | null; score: number | null }[] } {
  const i = lastAtOrBefore(
    h.board_history.map((b) => b[0]),
    cut,
  )
  if (i < 0) return { time: null, rows: [] }
  const [time, rows] = h.board_history[i]
  return { time, rows: rows.map(([symbol, change_pct, score], k) => ({ rank: k + 1, symbol, name: h.names[symbol] ?? '', change_pct, score })) }
}

/** 那一刻某檔的價格：秒級有就用那一秒以前最後一筆，沒有就用那一分鐘以前最後一根 K 的收盤；都沒有 → null。 */
export function priceAt(h: DayTradeHistory, symbol: string, cut: string): number | null {
  const c = h.charts[symbol]
  if (!c) return null
  if (c.secs && c.secs.s.length) {
    const base = secOf(c.secs.t0)
    const labels = c.secs.s.map((k) => hhmmss(base + k))
    const i = lastAtOrBefore(labels, cut)
    if (i >= 0) return c.secs.p[i]
  }
  const done = c.bars.filter((b) => b[0] < cut.slice(0, 5))
  return done.length ? done[done.length - 1][4] : null
}

export type ReplayDay = {
  cut: string
  /** 到 cut 為止已經賣掉的 */
  closed: DayTrade[]
  /** cut 那一刻還抱著的（進場 ≤ cut < 出場），open_pnl 用那一刻的價格粗估（只扣買進手續費，賣出成本到出場才算） */
  holding: (DayTrade & { price: number | null })[]
  realized: number
  events: DayEvent[]
}

/** 拉到 cut 那一刻：只用那之前發生的事（不劇透後面）。 */
export function replayAt(d: DayTradeDay, h: DayTradeHistory | null, cut: string): ReplayDay {
  const closed = d.trades.filter((t) => within(t.exit_time, cut))
  const holding = d.trades
    .filter((t) => within(t.entry_time, cut) && !within(t.exit_time, cut))
    .map((t) => ({ ...t, price: h ? priceAt(h, t.symbol, cut) : null }))
  return { cut, closed, holding, realized: closed.reduce((a, t) => a + t.net_pnl, 0), events: d.events.filter((e) => within(e.time, cut)) }
}

/** 那一刻抱著的這筆，用 price 賣掉的話大約賺賠多少（元；1 張 = 1,000 股，不含賣出手續費與證交稅）。 */
export const roughOpenPnl = (t: { entry_price: number; quantity: number }, price: number | null): number | null =>
  price === null ? null : Math.round((price - t.entry_price) * t.quantity * 1000)

export const secOf = (hhmmss: string): number =>
  Number(hhmmss.slice(0, 2)) * 3600 + Number(hhmmss.slice(3, 5)) * 60 + Number(hhmmss.slice(6, 8) || 0)

const pad = (n: number) => String(n).padStart(2, '0')
export const hhmmss = (sec: number): string => `${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec / 60) % 60)}:${pad(sec % 60)}`

/** 百分比寫法：+1.23%／-0.45%；null → —。 */
export const pct = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`

/** 元：+12,345／-6,789。 */
export function yuan(v: number): string {
  const r = Math.round(v)
  if (r === 0) return '0'
  return `${r > 0 ? '+' : '-'}${Math.abs(r).toLocaleString('en-US')}`
}

/** 漲跌色：台股漲紅跌綠。 */
export const tone = (v: number | null | undefined): 'up' | 'down' | 'plain' => (v === null || v === undefined || v === 0 ? 'plain' : v > 0 ? 'up' : 'down')

/** 盤中要不要每 15 秒重抓：今天、台北 08:25～13:45 之間。 */
export function shouldPoll(now: Date, dayShown: string | null): boolean {
  const t = new Date(now.getTime() + 8 * 3600_000).toISOString()
  const day = t.slice(0, 10)
  const hhmm = t.slice(11, 16)
  return (dayShown === null || dayShown === day) && hhmm >= '08:25' && hhmm <= '13:45'
}

/** 一檔在榜上的狀態：持有中、今天做過、只是在榜上。 */
export function boardTag(symbol: string, d: Pick<DayTradeDay, 'positions' | 'trades'>): 'holding' | 'traded' | null {
  if (d.positions.some((p) => p.symbol === symbol)) return 'holding'
  if (d.trades.some((t) => t.symbol === symbol)) return 'traded'
  return null
}
