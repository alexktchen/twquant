// GET /api/daytrade/days：有當沖紀錄的交易日（新到舊），每天的來源、狀態、帳戶合計、成交筆數。權限同台指期（T-458／D-221）。
import { guarded, json, type Env } from '../../_lib/db'
import { DAYS_LIMIT } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { readRows } from '../../_lib/scorecard'

type Row = { day: string; source: string; status: string; total: number | null; closed: number | null }

const handle: PagesFunction<Env> = async ({ env }) =>
  guarded(env, async (db) => {
    const days = await readRows(
      () => db<Row[]>`
        SELECT day::text AS day, source, status, (payload->'account'->>'total')::float8 AS total,
               (payload->'account'->>'closed')::int AS closed
        FROM serving.stock_paper ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    return json({ days })
  })

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
