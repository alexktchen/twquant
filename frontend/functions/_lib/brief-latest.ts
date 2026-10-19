// 首頁「今日一句話」要顯示哪一份（T-409，2026-10-19 使用者：「這邊台灣晚上時間也要跑一次」）：
// 盤前晨報（06:30）與盤後快訊（21:45）各存一張表，首頁拿 generated_at 最新的那份；純函式，供單元測試。
export type Edition = 'morning' | 'evening'
export type Dated = { generated_at: string; edition: Edition }

/** 兩份裡 generated_at 較新的；只有一份就是那份；都沒有 → null。同時間（不該發生）取盤後。 */
export function latestOf<T extends Dated>(morning: T | null, evening: T | null): T | null {
  if (!morning) return evening
  if (!evening) return morning
  return evening.generated_at >= morning.generated_at ? evening : morning
}
