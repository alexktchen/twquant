// 台指期對照策略與檢討單（T-467）的純函數。對照策略來自 fubon_live 的 payload.extras，都沒通過驗證，只記帳。

export type ExtraSleeveKey = 'NOISE_LONG' | 'NOISE_SHORT' | 'BOX_LONG' | 'BOX_SHORT' | 'TAIL_SHORT'
export type ExtraTrade = {
  sleeve: ExtraSleeveKey
  side: 1 | -1
  entry_time: string
  entry_price: number
  exit_time: string
  exit_price: number
  exit_reason: string
  points: number
  net_pnl: number
}
export type ExtraSleeve = { name: string; position: number; entry_price: number | null; realized: number; open_pnl: number | null; closed: number }
export type ExtraEvent = { time: string; kind: string; sleeve: ExtraSleeveKey; text: string }
export type FuturesExtras = { version: 1; sleeves: Record<ExtraSleeveKey, ExtraSleeve>; trades: ExtraTrade[]; events: ExtraEvent[]; noise_ready: boolean }

export const EXTRA_GROUPS: readonly { name: string; sleeves: readonly ExtraSleeveKey[]; verdict: string }[] = [
  { name: '區間突圍', sleeves: ['NOISE_LONG', 'NOISE_SHORT'], verdict: '九年 +345,974 元，約七成來自 2026，t 1.79' },
  { name: '回測確認', sleeves: ['BOX_LONG', 'BOX_SHORT'], verdict: '九年 +124,091 元，t 0.59' },
  { name: '尾盤順勢空', sleeves: ['TAIL_SHORT'], verdict: '九年 +114,005 元，沒有單獨判定' },
]

const within = (time: string, cut: string): boolean => time.slice(0, cut.length) <= cut

/** 那一刻的樣子（復盤不劇透）：cut 空字串＝全部。 */
export function extrasAt(x: FuturesExtras, cut: string) {
  const seen = (t: string) => cut === '' || within(t, cut)
  const trades = x.trades.filter((t) => seen(t.exit_time))
  const holding = x.trades.filter((t) => seen(t.entry_time) && !seen(t.exit_time))
  const openNow = cut === '' ? (Object.keys(x.sleeves) as ExtraSleeveKey[]).filter((k) => x.sleeves[k].position !== 0) : holding.map((t) => t.sleeve)
  const groups = EXTRA_GROUPS.map((g) => {
    const rows = trades.filter((t) => g.sleeves.includes(t.sleeve))
    return { ...g, closed: rows.length, wins: rows.filter((t) => t.net_pnl > 0).length, realized: rows.reduce((a, t) => a + t.net_pnl, 0), holding: g.sleeves.filter((s) => openNow.includes(s)).length }
  })
  return { trades, events: x.events.filter((e) => seen(e.time)), groups }
}

export type JournalRow = {
  id: string
  date: string
  product: 'MTX' | 'TX' | 'TMF'
  side: 1 | -1
  time: string
  entry: number
  exit: number
  stop: number | null
  qty: number
  setup: string
  followed: boolean
  note: string
}
export type JournalList = { today: string; items: JournalRow[]; max: number }

export const POINT_VALUE: Record<JournalRow['product'], number> = { MTX: 50, TX: 200, TMF: 10 }
/** 判斷前要累積的筆數（使用者同意的「累積 100 筆」）。 */
export const JOURNAL_GOAL = 100

export const journalPoints = (r: JournalRow): number => r.side * (r.exit - r.entry)
export const journalYuan = (r: JournalRow): number => journalPoints(r) * POINT_VALUE[r.product] * r.qty
/** 用了幾倍風險；沒填停損或風險不是正數回 null。 */
export function journalR(r: JournalRow): number | null {
  if (r.stop === null) return null
  const risk = r.side * (r.entry - r.stop)
  return risk > 0 ? journalPoints(r) / risk : null
}

export type JournalStat = { name: string; n: number; win: number; avgPts: number; avgWin: number; avgLoss: number; breakeven: number | null; yuan: number }

/** 勝率、賺賠和打平勝率；不扣手續費與稅，打平勝率＝平均賠 ÷（平均賺＋平均賠）。 */
export function statOf(name: string, rows: readonly JournalRow[]): JournalStat {
  const pts = rows.map(journalPoints)
  const wins = pts.filter((p) => p > 0)
  const losses = pts.filter((p) => p < 0).map((p) => -p)
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0)
  const avgWin = mean(wins)
  const avgLoss = mean(losses)
  return {
    name,
    n: rows.length,
    win: rows.length ? wins.length / rows.length : 0,
    avgPts: mean(pts),
    avgWin,
    avgLoss,
    breakeven: avgWin + avgLoss > 0 && wins.length > 0 && losses.length > 0 ? avgLoss / (avgWin + avgLoss) : null,
    yuan: rows.reduce((a, r) => a + journalYuan(r), 0),
  }
}

/** 全部、依進場理由、依有沒有照計畫。筆數多的在前。 */
export function journalStats(rows: readonly JournalRow[]) {
  const by = new Map<string, JournalRow[]>()
  for (const r of rows) by.set(r.setup, [...(by.get(r.setup) ?? []), r])
  return {
    all: statOf('全部', rows),
    setups: [...by].map(([k, v]) => statOf(k, v)).sort((a, b) => b.n - a.n),
    followed: statOf('照計畫', rows.filter((r) => r.followed)),
    broke: statOf('沒照計畫', rows.filter((r) => !r.followed)),
  }
}
