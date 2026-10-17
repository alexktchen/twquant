// 洞悉證據包端點的查詢參數（T-373）。純函數，方便測試；資料庫存取在 api/platform/insights.ts。
import { SID_RE } from './picks.ts'

export const DEFAULT_LIMIT = 10
export const MAX_LIMIT = 100

export type InsightQuery = { ok: true; stock: string | null; limit: number } | { ok: false; detail: string }

/** `?stock=2330&limit=5`：stock 可省（列最新的幾份）；limit 不是 1～100 的整數就用預設，不猜。 */
export function parseInsightQuery(url: URL): InsightQuery {
  const stock = url.searchParams.get('stock')
  if (stock !== null && !SID_RE.test(stock)) return { ok: false, detail: `非法股票代號：${stock}` }
  const raw = url.searchParams.get('limit')
  const n = raw === null ? DEFAULT_LIMIT : Number(raw)
  const limit = Number.isInteger(n) && n >= 1 && n <= MAX_LIMIT ? n : DEFAULT_LIMIT
  return { ok: true, stock, limit }
}
