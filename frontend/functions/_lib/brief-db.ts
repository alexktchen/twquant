// 「今日一句話」（晨報）的讀取：首頁讀最新一天，歷史頁（使用者 2026-10-17：「做一個可以選日期看歷史的頁面」）讀指定那天。
// 晨報每天 06:30 寫一筆存在 serving.morning_brief，看網頁不會重叫模型；表還沒建 → 沒有（readRows）。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

type Db = ReturnType<typeof sql>
type Row = Record<string, unknown>

/** 那天的晨報；day 為 null → 最新一天。 */
export async function selectBrief(db: Db, day: string | null): Promise<Row | null> {
  const [row] = await readRows(
    () => db<Row[]>`
      SELECT date::text AS date, to_char(generated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS generated_at, rule_text, headline, bullets, risk_note,
             model_id, absent_reason, facts, inputs->'extras' AS extras, inputs->'adr_premium' AS adr_premium,
             inputs->'headlines' AS headlines
      FROM serving.morning_brief
      WHERE ${day}::date IS NULL OR date = ${day}::date
      ORDER BY date DESC LIMIT 1`,
  )
  return row ?? null
}

/** 有晨報的日期，新到舊，最多 limit 天。 */
export async function briefDates(db: Db, limit: number): Promise<string[]> {
  const rows = await readRows(
    () => db<{ d: string }[]>`SELECT date::text AS d FROM serving.morning_brief ORDER BY date DESC LIMIT ${limit}`,
  )
  return rows.map((r) => r.d)
}

/** 那天大盤實際收盤（晨報寫在開盤前，拿來對照）；假日或當天還沒收盤 → null。 */
export async function selectActual(db: Db, day: string): Promise<Row | null> {
  const [row] = await readRows(
    () => db<{ j: Row }[]>`
      SELECT jsonb_build_object('date', date::text, 'taiex_close', taiex_close, 'taiex_chg', taiex_chg,
                                'otc_close', otc_close, 'otc_chg', otc_chg, 'foreign_net', foreign_net) AS j
      FROM serving.market_daily WHERE date = ${day}::date`,
  )
  return row?.j ?? null
}
