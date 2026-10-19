// 「今日一句話」（晨報）的讀取：首頁讀最新一天，歷史頁（使用者 2026-10-17：「做一個可以選日期看歷史的頁面」）讀指定那天。
// 晨報每天 06:30 寫一筆存在 serving.morning_brief，看網頁不會重叫模型；表還沒建 → 沒有（readRows）。
// 盤後快訊（T-409，18:00）存 serving.evening_brief；首頁顯示兩張表裡 generated_at 最新的那份（latestOf）；
// 歷史清單一天兩格（盤前、盤後，使用者 2026-10-20：「可以看歷史一天兩則」）。
import { latestOf, mergeBriefKeys, type Edition } from './brief-latest.ts'
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

type Db = ReturnType<typeof sql>
type Row = Record<string, unknown>
type BriefRow = Row & { generated_at: string; edition: Edition }

/** 那天的晨報；day 為 null → 最新一天。 */
export async function selectBrief(db: Db, day: string | null): Promise<Row | null> {
  return selectEdition(db, 'morning', day)
}

/** 某張表（盤前或盤後）那天的一份；day 為 null → 最新一天。表還沒建 → null（readRows）。兩張表欄位相同，查詢寫兩次是為了不用動態表名。 */
export async function selectEdition(db: Db, edition: Edition, day: string | null): Promise<BriefRow | null> {
  const rows = await readRows(() =>
    edition === 'evening'
      ? db<Row[]>`
      SELECT date::text AS date, to_char(generated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS generated_at, rule_text, headline, bullets, risk_note,
             model_id, absent_reason, facts, inputs->'extras' AS extras, inputs->'adr_premium' AS adr_premium,
             inputs->'headlines' AS headlines
      FROM serving.evening_brief
      WHERE ${day}::date IS NULL OR date = ${day}::date
      ORDER BY date DESC LIMIT 1`
      : db<Row[]>`
      SELECT date::text AS date, to_char(generated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS generated_at, rule_text, headline, bullets, risk_note,
             model_id, absent_reason, facts, inputs->'extras' AS extras, inputs->'adr_premium' AS adr_premium,
             inputs->'headlines' AS headlines
      FROM serving.morning_brief
      WHERE ${day}::date IS NULL OR date = ${day}::date
      ORDER BY date DESC LIMIT 1`,
  )
  const row = rows[0]
  return row ? ({ ...row, edition } as BriefRow) : null
}

/** 首頁那份：盤前與盤後各取最新一天，再挑 generated_at 較新的（晚上看到盤後、隔天早上換成盤前）。 */
export async function selectLatestBrief(db: Db): Promise<Row | null> {
  const [m, e] = await Promise.all([selectEdition(db, 'morning', null), selectEdition(db, 'evening', null)])
  return latestOf(m, e)
}

/** 有晨報的日期，新到舊，最多 limit 天。 */
export async function briefDates(db: Db, limit: number): Promise<string[]> {
  const rows = await readRows(
    () => db<{ d: string }[]>`SELECT date::text AS d FROM serving.morning_brief ORDER BY date DESC LIMIT ${limit}`,
  )
  return rows.map((r) => r.d)
}

/** 歷史清單：盤前與盤後各自的日期合成「日期|版本」，新到舊、最多 limit 格；哪張表還沒建就只有另一張的。 */
export async function briefKeys(db: Db, limit: number): Promise<string[]> {
  const morning = await briefDates(db, limit)
  const evening = await readRows(
    () => db<{ d: string }[]>`SELECT date::text AS d FROM serving.evening_brief ORDER BY date DESC LIMIT ${limit}`,
  )
  return mergeBriefKeys(morning, evening.map((r) => r.d), limit)
}

/** 盤後快訊（寫在收盤後，講的是下一個交易日）要對照的：那天之後第一個交易日的實際收盤；還沒有 → null。 */
export async function selectActualAfter(db: Db, day: string): Promise<Row | null> {
  const [row] = await readRows(
    () => db<{ j: Row }[]>`
      SELECT jsonb_build_object('date', date::text, 'taiex_close', taiex_close, 'taiex_chg', taiex_chg,
                                'otc_close', otc_close, 'otc_chg', otc_chg, 'foreign_net', foreign_net) AS j
      FROM serving.market_daily WHERE date > ${day}::date ORDER BY date LIMIT 1`,
  )
  return row?.j ?? null
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
