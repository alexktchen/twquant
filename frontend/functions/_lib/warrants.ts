// 個股頁的權證摘要（T-385／D-191 方向 4）：serving.warrant_summary_daily（每天由 pipeline「權證」步驟算好）。
// 只供看盤，不改名單、不進模型。純函數放這裡供 node 單元測試；/api/stocks/:sid/warrants 讀資料庫後呼叫。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 個股頁列幾天。 */
export const WARRANT_DAYS = 5

export type WarrantDay = {
  date: string
  n_call: number
  n_put: number
  call_value: number
  put_value: number
  underlying_amount: number | null
  /** （認購＋認售成交金額）÷ 標的成交金額 */
  value_ratio: number | null
  /** 價平認購／認售隱含波動（年化小數）；算不出來 null */
  iv_call: number | null
  iv_put: number | null
  iv_call_days: number | null
  iv_put_days: number | null
}

type Raw = Record<keyof WarrantDay, unknown>
const num = (x: unknown): number | null => (x === null || x === undefined ? null : Number(x))

export function warrantDays(rows: Raw[]): WarrantDay[] {
  return rows
    .map((r) => ({
      date: String(r.date),
      n_call: Number(r.n_call),
      n_put: Number(r.n_put),
      call_value: Number(r.call_value),
      put_value: Number(r.put_value),
      underlying_amount: num(r.underlying_amount),
      value_ratio: num(r.value_ratio),
      iv_call: num(r.iv_call),
      iv_put: num(r.iv_put),
      iv_call_days: num(r.iv_call_days),
      iv_put_days: num(r.iv_put_days),
    }))
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0)) // 新到舊
}

/** 認售占（認購＋認售）成交金額的比例；都是 0 → null。 */
export function putShare(d: { call_value: number; put_value: number }): number | null {
  const total = d.call_value + d.put_value
  return total > 0 ? d.put_value / total : null
}

type Db = ReturnType<typeof sql>

/** 到 today 為止最近幾天。表還沒建 → 空。 */
export async function readWarrantDays(db: Db, sid: string, today: string): Promise<WarrantDay[]> {
  const rows = await readRows(
    () => db<Raw[]>`
      SELECT date::text AS date, n_call, n_put, call_value, put_value, underlying_amount, value_ratio,
             iv_call, iv_put, iv_call_days, iv_put_days
      FROM serving.warrant_summary_daily
      WHERE underlying_id = ${sid} AND date <= ${today}::date
      ORDER BY date DESC LIMIT ${WARRANT_DAYS}`,
  )
  return warrantDays(rows)
}
