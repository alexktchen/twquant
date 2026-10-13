// 成績單（D-176）的型別與純函數：淨值曲線換成累積報酬、即時／事後補分段、逐筆的白話標籤。不碰 DOM，供 node 單元測試。

/** 一段淨值的數字（全部／即時公布以來）。bench_* 是同一段的 0050；算不出來是 null。 */
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
  }
  gaps: string[]
  trades: {
    hold_sessions: number
    big_loss: number
    notional: number
    costs: { commission: number; tax: number }
    stats: { all: TradeStats; backfill: TradeStats; live: TradeStats; open: number; unfilled: number; pending: number }
  }
}

type Pt = { time: string; value: number }

/**
 * 圖上的三條線（累積報酬 %，起點＝0）：事後補那段的帳戶、即時公布以來的帳戶、同期 0050。
 * 兩段帳戶在即時第一天接起來（那天兩條都有點），圖上才是連續的一條。沒有即時段＝全部是事後補。
 */
export function navLines(nav: NavPoint[], liveSince: string | null): { backfill: Pt[]; live: Pt[]; bench: Pt[] } {
  const out = { backfill: [] as Pt[], live: [] as Pt[], bench: [] as Pt[] }
  const v0 = nav[0]?.v
  const b0 = nav[0]?.b ?? null
  if (!v0) return out
  for (const p of nav) {
    const pt = { time: p.d, value: (p.v / v0 - 1) * 100 }
    if (liveSince === null || p.d <= liveSince) out.backfill.push(pt)
    if (liveSince !== null && p.d >= liveSince) out.live.push(pt)
    if (b0 !== null && p.b !== null) out.bench.push({ time: p.d, value: (p.b / b0 - 1) * 100 })
  }
  return out
}

/** 帳戶相對 0050 多賺（正）或少賺（負）幾個百分點；任一個沒有就是 null。 */
export function excess(s: NavSummary | null): number | null {
  if (!s || s.bench_ret === null) return null
  return s.ret - s.bench_ret
}

/** 「比 0050」那格：多賺／少賺幾個百分點；差不到 0.05 個百分點寫「差不多」。 */
export function beatText(ex: number | null): string {
  if (ex === null || !Number.isFinite(ex)) return '—'
  const pp = Math.abs(ex * 100)
  if (pp < 0.05) return '差不多'
  return `${ex > 0 ? '多賺' : '少賺'} ${pp.toFixed(1)} 個百分點`
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
