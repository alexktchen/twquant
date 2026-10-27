// GET /api/futures/quotes/days?product=TX|TMF：有這個商品行情的交易日（新到舊）與來源、狀態；有模擬單的日子多合計與收盤重算是否一致。只給 admin（T-451）。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { DAYS_LIMIT, productOk } from '../../../_lib/futures'
import { denied } from '../../../_lib/gate'
import { readRows } from '../../../_lib/scorecard'

type Row = { day: string; source: string; status: string; total: number | null; match: boolean | null }

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const product = new URL(request.url).searchParams.get('product')
  if (!productOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const days = await readRows(
      () => db<Row[]>`
        SELECT day::text AS day, source, status, (payload->>'total')::float8 AS total,
               (payload->>'official_match')::boolean AS match FROM serving.futures_quotes
        WHERE product = ${product} ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    return json({ days })
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
