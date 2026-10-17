// 內部人持股轉讓事前申報（T-384）：董監事、經理人、大股東申報要賣股票。只供看盤（名單小標、個股頁行事曆），不改名單、不進模型。
// 純函數放這裡供 node 單元測試；/api/picks 與 /api/stocks/:sid/calendar 讀資料庫後呼叫。資料表 serving.insider_transfer_notices。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 名單小標往回看幾天的申報（申報的有效轉讓期間通常是一個月）。 */
export const INSIDER_LOOKBACK_DAYS = 30

export type InsiderNotice = {
  /** 出表日期（申報公開那天） */
  date: string
  role: string
  person: string | null
  method: string
  /** 預定轉讓股數（股）；表上算不出來是 null */
  planned_shares: number | null
  period_start: string | null
  period_end: string | null
  transferee: string | null
}

/** 名單一列的內部人申讓摘要：近 INSIDER_LOOKBACK_DAYS 天有幾筆、合計股數（有數字的加總）、最近一筆的日期與身分。 */
export type InsiderFlag = { n: number; shares: number | null; latest: string; roles: string[] }

const num = (x: unknown): number | null => (x === null || x === undefined ? null : Number(x))

/** 「10/19～11/18」；只有一端或都沒有就省略。 */
function periodText(start: string | null, end: string | null): string {
  const md = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
  if (start && end) return `${md(start)}～${md(end)}`
  return ''
}

/** 股數 → 「200 萬股」「5,000 股」；null → ''。 */
export function sharesText(shares: number | null): string {
  if (shares === null || !Number.isFinite(shares)) return ''
  if (shares >= 10_000) return `${Number((shares / 10_000).toFixed(1)).toLocaleString('zh-TW')} 萬股`
  return `${Math.round(shares).toLocaleString('zh-TW')} 股`
}

/** 一筆申報的一句話：「董事本人 兆淞投資有限公司 鉅額逐筆交易 200 萬股（10/19～11/18）」；贈與／信託加受讓人。 */
export function insiderText(r: InsiderNotice): string {
  const parts = [r.role, r.person, r.method, sharesText(r.planned_shares)].filter((s) => s && s.trim())
  const period = periodText(r.period_start, r.period_end)
  const to = r.transferee ? `，受讓人 ${r.transferee}` : ''
  return `${parts.join(' ')}${period ? `（${period}）` : ''}${to}`
}

/** 多筆申報 → 名單小標用的摘要；沒有 → null。 */
export function insiderFlag(rows: InsiderNotice[]): InsiderFlag | null {
  if (rows.length === 0) return null
  const sorted = [...rows].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))
  const known = rows.map((r) => r.planned_shares).filter((s): s is number => s !== null && Number.isFinite(s))
  const roles = [...new Set(rows.map((r) => r.role))]
  return { n: rows.length, shares: known.length ? known.reduce((a, b) => a + b, 0) : null, latest: sorted[0].date, roles }
}

type Db = ReturnType<typeof sql>

/** date 當天（含）往回 INSIDER_LOOKBACK_DAYS 天內、名單股的申報 → 每檔一份摘要。表還沒建 → 空。 */
export async function readInsider(db: Db, date: string, sids: string[]): Promise<Map<string, InsiderFlag>> {
  const out = new Map<string, InsiderFlag>()
  if (sids.length === 0) return out
  const rows = await readRows(
    () => db<(InsiderNotice & { stock_id: string })[]>`
      SELECT stock_id, date::text AS date, role, person, method, planned_shares,
             period_start::text AS period_start, period_end::text AS period_end, transferee
      FROM serving.insider_transfer_notices
      WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ',')) AND date <= ${date}::date
        AND date > ${date}::date - ${INSIDER_LOOKBACK_DAYS}::int`,
  )
  for (const sid of sids) {
    const mine = rows.filter((r) => r.stock_id === sid).map((r) => ({ ...r, planned_shares: num(r.planned_shares) }))
    const flag = insiderFlag(mine)
    if (flag) out.set(sid, flag)
  }
  return out
}
