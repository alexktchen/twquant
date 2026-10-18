// 個股頁行事曆的文字（使用者 2026-10-17：「這邊裡面的個股資訊要顯示行事曆」）。純函式，供 node 單元測試。
import { dayLabel, dividendText } from './war.ts'

export type StockCalItem = {
  date: string
  kind: 'cash' | 'stock' | 'both' | 'split' | 'reduce' | 'conf' | 'insider'
  time: string | null
  /** 法說會地點／說明；內部人申讓是「誰、怎麼賣、多少、期間」一句話（T-384） */
  summary: string | null
  cash_dividend: number | null
  stock_ratio: number | null
  rights_ratio: number | null
}

export const STOCK_CAL_KIND: Record<StockCalItem['kind'], string> = {
  cash: '除息',
  stock: '除權',
  both: '除權息',
  split: '股票分割',
  reduce: '減資',
  conf: '法說會',
  insider: '內部人申讓',
}

/** 今天（含）以後的照日期由近到遠，以前的由新到舊。 */
export function splitByToday(items: StockCalItem[], today: string): { upcoming: StockCalItem[]; past: StockCalItem[] } {
  const upcoming = items.filter((x) => x.date >= today).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
  const past = items.filter((x) => x.date < today).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))
  return { upcoming, past }
}

/** 日期：今年的寫「10/28（三）」，其他年份前面加年「2025/6/10（二）」。 */
export function calDate(day: string, today: string): string {
  return day.slice(0, 4) === today.slice(0, 4) ? dayLabel(day) : `${day.slice(0, 4)}/${dayLabel(day)}`
}

/** 這一筆的說明：除權息寫金額（預告才有；已發生的只有日期）、法說會寫時間、內部人申讓寫那一句話。 */
export function calDetail(x: StockCalItem): string {
  if (x.kind === 'conf') return x.time ?? ''
  if (x.kind === 'insider') return x.summary ?? ''
  if (x.kind === 'split' || x.kind === 'reduce') return ''
  return dividendText(x)
}

/** 距今天幾天：「今天」「明天」「3 天後」；過去的不寫。 */
export function daysAway(day: string, today: string): string {
  const n = Math.round((Date.parse(`${day}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000)
  if (n < 0) return ''
  if (n === 0) return '今天'
  if (n === 1) return '明天'
  return `${n} 天後`
}
