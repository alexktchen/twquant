// 「為什麼選它」（D-180）：serving.pick_reasons，名單上每一檔的白話理由（每日「名單理由」步驟算好存起來）。
// 表還沒建（新功能上線後第一次排程跑完之前）或那天沒算，當成沒有，名單照樣出來。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 一條理由：f＝因子、t＝白話、s＝+1 加分／−1 扣分／0 前提（大盤精選的排序模型門檻）。 */
export type Reason = { f: string; t: string; s: number }

type Db = ReturnType<typeof sql>

function parse(text: string | null): Reason[] | null {
  if (!text) return null
  const v: unknown = JSON.parse(text)
  if (!Array.isArray(v)) return null
  return v.filter((r): r is Reason => typeof r?.t === 'string' && typeof r?.s === 'number' && typeof r?.f === 'string')
}

/** `date` 那天每條線每一檔的理由，鍵＝「線|代號」。 */
export async function readReasons(db: Db, date: string): Promise<Map<string, Reason[]>> {
  const rows = await readRows(
    () => db<{ line: string; stock_id: string; reasons: string | null }[]>`
      SELECT line, stock_id, reasons::text AS reasons FROM serving.pick_reasons WHERE date = ${date}::date`,
  )
  const out = new Map<string, Reason[]>()
  for (const r of rows) {
    const list = parse(r.reasons)
    if (list?.length) out.set(`${r.line}|${r.stock_id}`, list)
  }
  return out
}
