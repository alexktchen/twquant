// 首頁「今日一句話」要顯示哪一份（T-409，2026-10-19 使用者：「這邊台灣晚上時間也要跑一次」）：
// 盤前晨報（06:30）與盤後快訊（21:45）各存一張表，首頁拿 generated_at 最新的那份；純函式，供單元測試。
export type Edition = 'morning' | 'evening'
export type Dated = { generated_at: string; edition: Edition }

/** 歷史清單的一格：「2026-10-20|evening」（使用者 2026-10-20：「可以看歷史一天兩則」）。 */
export const briefKey = (date: string, edition: Edition): string => `${date}|${edition}`
export function parseBriefKey(key: string): { date: string; edition: Edition } {
  const [date, ed] = key.split('|')
  return { date, edition: ed === 'evening' ? 'evening' : 'morning' }
}
/** 兩張表的日期合成一條清單，新到舊；同一天盤後（17:45）在盤前（06:30）前面。 */
export function mergeBriefKeys(morning: string[], evening: string[], limit: number): string[] {
  const all = [...morning.map((d) => ({ d, e: 'morning' as Edition })), ...evening.map((d) => ({ d, e: 'evening' as Edition }))]
  all.sort((a, b) => (a.d === b.d ? (a.e === 'evening' ? -1 : 1) : a.d < b.d ? 1 : -1))
  return all.slice(0, limit).map((x) => briefKey(x.d, x.e))
}

/** 兩份裡 generated_at 較新的；只有一份就是那份；都沒有 → null。同時間（不該發生）取盤後。 */
export function latestOf<T extends Dated>(morning: T | null, evening: T | null): T | null {
  if (!morning) return evening
  if (!evening) return morning
  return evening.generated_at >= morning.generated_at ? evening : morning
}
