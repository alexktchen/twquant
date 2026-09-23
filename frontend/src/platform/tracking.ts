/** 日期切換與連續天數的純函數（T-300／T-301）；頁面只負責畫。 */

/**
 * 在「有快照的日子」之間前後跳，不走日曆日——跳到沒有快照的日子只會看到空白。
 * `dates` 為 YYYY-MM-DD；current 不在清單裡（例如使用者選了沒有快照的日子）也找得到最近的兩側。
 */
export function neighbourDays(dates: readonly string[], current: string | null): { prev: string | null; next: string | null } {
  if (!current) return { prev: null, next: null }
  const sorted = [...new Set(dates)].sort()
  const prev = [...sorted].reverse().find(day => day < current) ?? null
  const next = sorted.find(day => day > current) ?? null
  return { prev, next }
}

/** 連續天數出現在畫面上的樣子；`open` 表示實際可能更久（原因由頁面另外說明）。 */
export type StreakOpen = 'lookback' | 'history' | 'failed' | null

export function streakText(days: number, open: StreakOpen): string {
  return `${open ? '≥' : ''}${days} 天`
}

/** 「≥」的原因，放在表格下方的一行說明。 */
export function streakReason(open: Exclude<StreakOpen, null>, lookback: number, earliest: string | null): string {
  if (open === 'lookback') return `只往回看 ${lookback} 份快照，實際可能更久`
  if (open === 'history') return `已追到最早一份快照${earliest ? `（${earliest}）` : ''}，更早沒有紀錄`
  return '更早那天這支策略計算失敗，無從得知是否在名單上'
}
