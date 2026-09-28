/** 每日選股看板（D-164）的資料型別與純函數；頁面只負責畫。 */

/**
 * `change`（D-165）：成交價算的股價漲跌（小數，0.05 = +5%），不含股利與費用；舊看板沒有這欄。
 * 新增那筆的 `change_to`：`exit`＝算到賣出、`now`＝仍持有，算到看板最後一天收盤。
 */
export type Entered = { stock_id: string; reason: string; change?: number | null; change_to?: 'exit' | 'now' }
export type Exited = { stock_id: string; reason: string; entry_date: string; held_sessions: number; change?: number | null }
export type Holding = { stock_id: string; entry_date: string; held_sessions: number; change?: number | null }
/** 當天模型前 n 名（推薦），位子滿不滿都列；`held`＝收盤後持有、`new`＝過去 20 個交易日沒進過前 n。 */
export type Pick = { stock_id: string; rank: number; held: boolean; new: boolean }
/** `watch_new`／`picks`：補位型才有。 */
export type BoardDay = { date: string; entered: Entered[]; exited: Exited[]; holdings: Holding[]; watch_new?: string[]; picks?: Pick[] | null; picks_missing?: string }
export type NextPlan = { rebalance: boolean; buy: string[]; sell: { stock_id: string; reason: string }[] }
export type BoardStrategy = {
  code: string
  name: string
  summary: string
  kind: 'rebalance' | 'slots'
  hold_sessions: number | null
  /** 補位型＝位子數；月換股型＝每次挑幾檔。 */
  slots: number | null
  /** 推薦名單順序是否就是模型名次（月換股型是等權重目標名單，沒有名次）。 */
  picks_ordered?: boolean
  /** 整段重播的每日淨值（D-165）；舊看板沒有。 */
  equity?: EquityPoint[]
  days: BoardDay[]
  next: NextPlan
}
export type StockInfo = { name?: string; industry?: string; market?: string }
export type Board = {
  as_of: string
  generated_at: string
  replay_start: string
  initial_capital?: number
  strategies: BoardStrategy[]
  stocks: Record<string, StockInfo>
}

/** 看板上有資料的交易日（由舊到新）；兩條線同一套日曆，取第一條即可。 */
export function boardDates(board: Board): string[] {
  return board.strategies[0]?.days.map(d => d.date) ?? []
}

/**
 * 使用者要看的那一天：沒指定或指定日比最新還晚 → 最新一天；指定到非交易日 → 往前找最近的交易日。
 * 比最早一天還早 → null（頁面說明「重播從哪天開始」）。
 */
export function resolveDay(dates: readonly string[], wanted: string | null): string | null {
  if (!dates.length) return null
  const last = dates[dates.length - 1]
  if (!wanted || wanted >= last) return last
  const earlier = dates.filter(d => d <= wanted)
  return earlier.length ? earlier[earlier.length - 1] : null
}

/** 賣出原因的白話。引擎的規則名（time_stop 等）不直接給人看。 */
export function exitReasonText(reason: string): string {
  if (reason === 'time_stop' || reason.startsWith('time_stop')) return '持有期滿'
  if (reason === 'rebalance') return '月換股移出'
  if (reason === 'stop_loss' || reason.startsWith('stop_loss')) return '停損'
  if (reason.startsWith('jump') || reason === 'forced') return '價格異常，強制出場'
  return reason
}

/** 補位型（抱滿 N 個交易日）還剩幾個交易日；月換股型沒有固定期限 → null。 */
export function remainingSessions(strategy: BoardStrategy, held: number): number | null {
  if (strategy.kind !== 'slots' || strategy.hold_sessions === null) return null
  return Math.max(strategy.hold_sessions - held, 0)
}

/** 有進出的日子（由新到舊），給「近三個月異動紀錄」。 */
export function changeLog(strategy: BoardStrategy): BoardDay[] {
  return strategy.days.filter(d => d.entered.length || d.exited.length).reverse()
}

/** 一檔股票在畫面上的名字：代號＋名稱（名稱缺時只給代號，代號一律原樣保留前導零）。 */
export function stockLabel(stocks: Record<string, StockInfo>, sid: string): string {
  const name = stocks[sid]?.name
  return name ? `${sid} ${name}` : sid
}

/** 漲跌的文字與顏色（台股慣例：紅漲綠跌）；沒有數字回 null，畫面就不顯示。 */
export function changeText(change: number | null | undefined): { text: string; tone: 'up' | 'down' | 'flat' } | null {
  if (change === null || change === undefined || !Number.isFinite(change)) return null
  const pctv = change * 100
  const text = `${pctv > 0 ? '+' : ''}${pctv.toFixed(1)}%`
  if (text === '+0.0%' || text === '-0.0%' || text === '0.0%') return { text: '0.0%', tone: 'flat' }
  return { text, tone: pctv > 0 ? 'up' : 'down' }
}

export type EquityPoint = { date: string; value: number }

/** 整體損益（D-165）：到 `day` 為止的累計、當天變化與最大回撤；淨值含費用與股利。 */
export function equitySummary(equity: readonly EquityPoint[], initial: number, day: string): {
  value: number; pnl: number; pnlPct: number; dayPnl: number | null; dayPct: number | null; maxDrawdown: number
} | null {
  const upto = equity.filter(p => p.date <= day)
  if (!upto.length || !(initial > 0)) return null
  const last = upto[upto.length - 1]
  const prev = upto.length > 1 ? upto[upto.length - 2] : null
  let peak = -Infinity
  let mdd = 0
  for (const p of upto) {
    peak = Math.max(peak, p.value)
    mdd = Math.min(mdd, p.value / peak - 1)
  }
  return {
    value: last.value,
    pnl: last.value - initial,
    pnlPct: last.value / initial - 1,
    dayPnl: prev ? last.value - prev.value : null,
    dayPct: prev && prev.value > 0 ? last.value / prev.value - 1 : null,
    maxDrawdown: mdd,
  }
}

/** 網址帶的分頁代號不存在 → 第一條線。 */
export function resolveTab(codes: readonly string[], wanted: string | null): string | null {
  if (!codes.length) return null
  return wanted && codes.includes(wanted) ? wanted : codes[0]
}

/**
 * 逐筆結果（到 `day` 為止、看板顯示窗內）：已賣出的筆數、平均每筆、勝率；持股的平均未實現漲跌。
 * 漲跌是成交價算的股價漲跌（不含股利與費用）。勝率只是輔助——隨機進場也有高勝率（D-136），看平均每筆。
 */
export function tradeStats(strategy: BoardStrategy, day: string): {
  closed: number; avgClosed: number | null; winRate: number | null; open: number; avgOpen: number | null; openUp: number
} {
  const closed = strategy.days
    .filter(d => d.date <= day)
    .flatMap(d => d.exited.map(e => e.change))
    .filter((x): x is number => typeof x === 'number' && Number.isFinite(x))
  const today = strategy.days.find(d => d.date === day)
  const open = (today?.holdings ?? [])
    .map(h => h.change)
    .filter((x): x is number => typeof x === 'number' && Number.isFinite(x))
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
  return {
    closed: closed.length,
    avgClosed: mean(closed),
    winRate: closed.length ? closed.filter(x => x > 0).length / closed.length : null,
    open: open.length,
    avgOpen: mean(open),
    openUp: open.filter(x => x > 0).length,
  }
}
