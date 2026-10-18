// GET /api/stocks/:sid/calendar：這一檔的行事曆（使用者 2026-10-17）——過去一年到往後 120 天的除權息、法說會、分割減資，
// 以及過去一年的內部人持股轉讓申報（T-384）。表還沒建 → 那個來源當沒有（readRows）。每個人看到都一樣，同一執行個體 60 秒暫存。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { cached, cacheKey } from '../../../_lib/cache'
import type { InsiderNotice } from '../../../_lib/insider'
import { SID_RE } from '../../../_lib/picks'
import { readRows } from '../../../_lib/scorecard'
import { STOCK_CAL_FUTURE_DAYS, STOCK_CAL_PAST_DAYS, stockCalendar } from '../../../_lib/stock-calendar'
import { addDays, taipeiDay } from '../../../_lib/warroom'

const handle: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  const today = taipeiDay(Date.now())
  const since = addDays(today, -STOCK_CAL_PAST_DAYS)
  const until = addDays(today, STOCK_CAL_FUTURE_DAYS)
  return guarded(env, async (db) => {
    const divs = await readRows(
      () => db<{ date: string; kind: string; cash_dividend: number | null; stock_ratio: number | null; rights_ratio: number | null }[]>`
        SELECT date::text AS date, kind, cash_dividend, stock_ratio, rights_ratio FROM serving.dividend_calendar
        WHERE stock_id = ${sid} AND date BETWEEN ${since}::date AND ${until}::date`,
    )
    const confs = await readRows(
      () => db<{ date: string; conf_time: string | null; location: string | null; summary: string | null }[]>`
        SELECT date::text AS date, conf_time, location, summary FROM serving.conference_calendar
        WHERE stock_id = ${sid} AND date BETWEEN ${since}::date AND ${until}::date`,
    )
    const past = await readRows(
      () => db<{ date: string; event_type: string }[]>`
        SELECT date::text AS date, event_type FROM serving.corporate_events
        WHERE stock_id = ${sid} AND date BETWEEN ${since}::date AND ${today}::date`,
    )
    const insiders = await readRows(
      () => db<InsiderNotice[]>`
        SELECT date::text AS date, role, person, method, planned_shares,
               period_start::text AS period_start, period_end::text AS period_end, transferee
        FROM serving.insider_transfer_notices
        WHERE stock_id = ${sid} AND date BETWEEN ${since}::date AND ${today}::date`,
    )
    return json({
      sid,
      today,
      items: stockCalendar(divs, confs, past, insiders.map((r) => ({ ...r, planned_shares: r.planned_shares === null ? null : Number(r.planned_shares) }))),
    })
  })
}

export const onRequestGet: PagesFunction<Env, 'sid'> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
