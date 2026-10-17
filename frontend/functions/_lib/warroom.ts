// 首頁戰情室（T-379／D-188）的純函數：重大訊息分類與篩選、行事曆、台北日期。讀資料庫的部分在 api/warroom/。
import { NEWS_CATEGORIES } from './news-categories.ts'

/** 昨日消息最多列幾則（全市場重要類別＋名單股）；追蹤股另外查、另外限。 */
export const NEWS_LIMIT = 40
export const WATCH_NEWS_LIMIT = 30
/** 行事曆看今天起幾天。 */
export const CALENDAR_DAYS = 3
/** 月營收法定申報期限：每月 10 日。 */
export const REVENUE_DEADLINE_DAY = 10

export type NewsRow = { stock_id: string; name: string | null; subject: string; at: string }
export type NewsItem = NewsRow & { tags: string[]; listed: boolean }

/** 主旨命中的分類（label）。 */
export function newsTags(subject: string): string[] {
  return NEWS_CATEGORIES.filter((c) => c.keywords.some((k) => subject.includes(k))).map((c) => c.label)
}

/** 主旨屬於全市場都要看的類別嗎（處置、變更交易、減增資、併購、庫藏股、停工訴訟、董總異動、重訊記者會）。 */
export function marketWide(subject: string): boolean {
  return NEWS_CATEGORIES.some((c) => c.market_wide && c.keywords.some((k) => subject.includes(k)))
}

/** 首頁的昨日消息：名單股的全留、其他只留全市場重要類別；新到舊、最多 limit 則。 */
export function pickNews(rows: NewsRow[], listed: Set<string>, limit: number = NEWS_LIMIT): NewsItem[] {
  const out: NewsItem[] = []
  for (const r of [...rows].sort((a, b) => b.at.localeCompare(a.at))) {
    const onList = listed.has(r.stock_id)
    if (!onList && !marketWide(r.subject)) continue
    out.push({ ...r, tags: newsTags(r.subject), listed: onList })
    if (out.length >= limit) break
  }
  return out
}

/** 某個時刻在台北的日期 YYYY-MM-DD（台灣沒有日光節約時間）。 */
export function taipeiDay(nowMs: number): string {
  return new Date(nowMs + 8 * 3600_000).toISOString().slice(0, 10)
}

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

/** 下一個月營收申報截止日（今天是 10 日就是今天）與還有幾天。 */
export function revenueDeadline(today: string): { date: string; days: number } {
  const [y, m, d] = today.split('-').map(Number)
  let yy = y
  let mm = m
  if (d > REVENUE_DEADLINE_DAY) {
    mm += 1
    if (mm === 13) {
      yy += 1
      mm = 1
    }
  }
  const date = `${yy}-${String(mm).padStart(2, '0')}-${String(REVENUE_DEADLINE_DAY).padStart(2, '0')}`
  const days = Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000)
  return { date, days }
}

/** 追蹤股查詢參數 ?sids=2330,2317：只收合法代號、去重、最多 100 檔；沒有就是空的。 */
export function parseSids(raw: string | null, re: RegExp, max = 100): string[] {
  if (!raw) return []
  return [...new Set(raw.split(',').map((s) => s.trim()).filter((s) => re.test(s)))].slice(0, max)
}
