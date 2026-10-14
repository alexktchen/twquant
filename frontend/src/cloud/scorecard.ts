// 名單績效（D-176，網站分頁「績效」）的型別與純函數：淨值曲線換成累積報酬、即時／事後補分段、逐筆的白話標籤。不碰 DOM，供 node 單元測試。

/** 一段淨值的數字（全部／即時公布以來）。bench_* 是同一段的 0050（同期買進抱著、含息）；算不出來是 null。 */
export type NavSummary = {
  start: string
  n_days: number
  ret: number
  cagr: number
  mdd: number
  short: boolean
  bench_ret: number | null
  bench_mdd: number | null
}

/** 已賣出的逐筆統計；n＝0 時其他都是 null（不是 0）。 */
export type TradeStats = {
  n: number
  mean: number | null
  median: number | null
  win_rate: number | null
  big_loss_rate: number | null
  best: number | null
  worst: number | null
}

export type TradeRow = {
  signal: string
  sid: string
  name: string | null
  status: 'closed' | 'open' | 'unfilled' | 'pending'
  entry_date: string | null
  entry_price: number | null
  exit_date: string | null
  exit_price: number | null
  held: number
  ret: number | null
  reason: string | null
  live: boolean
}

/** 模擬帳戶自己的一筆買賣（新到舊）。賣出的 ret＝賣價對這一輪平均買價的價差（不含費用與股利）；買進是 null。 */
export type AccountTrade = {
  date: string
  sid: string
  name: string | null
  side: 'buy' | 'sell'
  shares: number
  price: number
  reason: string
  /** open 新買進／add 已持有再買（換股日調回等權）／trim 賣一部分（調回等權）／close 出清 */
  move: 'open' | 'add' | 'trim' | 'close'
  ret: number | null
}

/** 淨值一天：d 日期、v 帳戶淨值、b 同期 0050（同樣起始資金）、x 持股比例。 */
export type NavPoint = { d: string; v: number; b: number | null; x: number | null }

export type Scorecard = {
  code: string
  name: string
  kind: 'slots' | 'rebalance'
  slots: number
  summary: string
  as_of: string
  generated_at: string
  start: string
  initial_capital: number
  live_since: string | null
  benchmark: { code: string; error: string | null }
  nav: NavPoint[]
  account: {
    all: NavSummary
    live: NavSummary | null
    equity: number
    cash: number
    holdings: { sid: string; name: string | null; entry_date: string | null; weight: number; ret: number }[]
    /** 帳戶買賣紀錄；這欄加上之前算的資料沒有它（下一次排程後就有） */
    trades?: AccountTrade[]
  }
  gaps: string[]
  trades: {
    hold_sessions: number
    big_loss: number
    notional: number
    costs: { commission: number; tax: number }
    stats: {
      all: TradeStats
      backfill: TradeStats
      live: TradeStats
      open: number
      unfilled: number
      pending: number
      /** 還抱著同一檔時又進名單、不重複算的次數（舊資料沒有這欄） */
      repeat?: number
    }
  }
}

type Pt = { time: string; value: number }

/**
 * 圖上的曲線（累積報酬 %，起點＝0）：帳戶事後補那段、即時公布以來那段，以及同期 0050（D-176 修訂三）。
 * 帳戶兩段在即時第一天接起來（那天兩條都有點），圖上才是連續的一條。沒有即時段＝全部是事後補。
 * 0050 從帳戶第一天起算；第一天就沒有 0050 的值（雲端還沒補它的行情）→ 不畫，中間缺的日子跳過。
 */
export function navLines(nav: NavPoint[], liveSince: string | null): { backfill: Pt[]; live: Pt[]; bench: Pt[] } {
  const out = { backfill: [] as Pt[], live: [] as Pt[], bench: [] as Pt[] }
  const v0 = nav[0]?.v
  if (!v0) return out
  const b0 = nav[0].b
  for (const p of nav) {
    const pt = { time: p.d, value: (p.v / v0 - 1) * 100 }
    if (liveSince === null || p.d <= liveSince) out.backfill.push(pt)
    if (liveSince !== null && p.d >= liveSince) out.live.push(pt)
    if (b0 && p.b !== null) out.bench.push({ time: p.d, value: (p.b / b0 - 1) * 100 })
  }
  return out
}

/** 明細那一列的大字：中文股名；資料庫查不到名字才退回代號（代號另外以小字列在下面）。 */
export function stockTitle(r: Pick<TradeRow, 'sid' | 'name'>): string {
  const name = r.name?.trim()
  return name ? name : r.sid
}

/** 逐筆狀態的白話。 */
export function statusText(r: TradeRow): string {
  if (r.status === 'closed') return r.reason === 'price_jump' ? '已賣（價格異常）' : '已賣'
  if (r.status === 'open') return '持有中'
  if (r.status === 'pending') return '明天開盤買'
  return `買不到（${UNFILLED[r.reason ?? ''] ?? r.reason ?? '原因不明'}）`
}

const UNFILLED: Record<string, string> = {
  limit_up: '開盤漲停',
  suspended: '停牌',
  price_jump: '價格異常',
  participation: '量太小',
  lot: '金額不足一股',
}

export type RowFilter = 'all' | 'live' | 'backfill'

/** 明細篩選：全部、只看即時、只看事後補。 */
export function filterRows(rows: TradeRow[], f: RowFilter): TradeRow[] {
  if (f === 'all') return rows
  return rows.filter((r) => r.live === (f === 'live'))
}

/** 明細看哪一種：已賣出（預設，看得到賺賠）、還沒賣（持有中＋明天才買）、全部。 */
export type StatusFilter = 'closed' | 'open' | 'all'

const isOpen = (r: TradeRow) => r.status === 'open' || r.status === 'pending'

/**
 * 明細要列的筆數與順序。已賣出依賣出日新到舊（最近賣掉的在最上面）；其他照公布日新到舊（資料原本的順序）。
 * 以前一律照公布日排，最上面幾十筆全是最近才進名單、還沒抱滿的——看起來像「只有買、沒有賣」。
 */
export function viewRows(rows: TradeRow[], status: StatusFilter, live: RowFilter): TradeRow[] {
  const base = filterRows(rows, live)
  if (status === 'open') return base.filter(isOpen)
  if (status === 'all') return base
  return base
    .filter((r) => r.status === 'closed')
    .sort((a, b) => (b.exit_date ?? '').localeCompare(a.exit_date ?? '') || b.signal.localeCompare(a.signal) || a.sid.localeCompare(b.sid))
}

/** 篩選鈕上的筆數。 */
export function statusCounts(rows: TradeRow[]): Record<StatusFilter, number> {
  return { closed: rows.filter((r) => r.status === 'closed').length, open: rows.filter(isOpen).length, all: rows.length }
}

/** 帳戶買賣的原因（白話）。kind：這條線是每月換股還是補位。 */
export function tradeReason(t: Pick<AccountTrade, 'side' | 'reason' | 'move'>, kind: Scorecard['kind']): string {
  // 換股日已持有的股票只是把權重調回平均（加碼或減碼），不是又買一次或賣掉
  if (t.move === 'add') return '加碼（調回平均）'
  if (t.move === 'trim') return '減碼（調回平均）'
  if (t.side === 'buy') return t.reason === 'entry' ? '空位補進' : '換股買進'
  if (t.reason.startsWith('time_stop')) return '抱滿到期'
  // 補位型不會換股：它的 rebalance 只可能是收盤價異常跳動（減資、停牌復牌）被強制出場
  if (t.reason === 'rebalance') return kind === 'rebalance' ? '換股賣出' : '價格異常出場'
  return t.reason
}

/** 「2026-05-04」→「5/4」。 */
export function md(d: string | null): string {
  if (!d) return '—'
  const [, m, day] = d.split('-')
  return `${Number(m)}/${Number(day)}`
}

/** 比例 → 「48%」（null → —）。 */
export function ratio(v: number | null): string {
  return v === null || !Number.isFinite(v) ? '—' : `${Math.round(v * 100)}%`
}

/** 金額 → 「1,023 萬」。 */
export function wan(v: number): string {
  return `${Math.round(v / 10_000).toLocaleString('zh-TW')} 萬`
}
