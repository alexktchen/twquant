/** 每日選股看板（D-164）的資料型別與純函數；頁面只負責畫。 */

export type Entered = { stock_id: string; reason: string }
export type Exited = { stock_id: string; reason: string; entry_date: string; held_sessions: number }
export type Holding = { stock_id: string; entry_date: string; held_sessions: number }
/** `watch_new`：補位型才有——這天模型前 n 名裡、過去 20 個交易日沒進過前 n 的新名字（觀察用，不一定會買）。 */
export type BoardDay = { date: string; entered: Entered[]; exited: Exited[]; holdings: Holding[]; watch_new?: string[] }
export type NextPlan = { rebalance: boolean; buy: string[]; sell: { stock_id: string; reason: string }[] }
export type BoardStrategy = {
  code: string
  name: string
  summary: string
  kind: 'rebalance' | 'slots'
  hold_sessions: number | null
  /** 補位型＝位子數；月換股型＝每次挑幾檔。 */
  slots: number | null
  days: BoardDay[]
  next: NextPlan
}
export type StockInfo = { name?: string; industry?: string; market?: string }
export type Board = {
  as_of: string
  generated_at: string
  replay_start: string
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
