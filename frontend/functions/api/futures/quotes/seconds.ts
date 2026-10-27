// GET /api/futures/quotes/seconds?product=TX|TMF&day=YYYY-MM-DD：大台／微台那一天的秒級復盤資料（同小台的編碼）。只給 admin（T-451）。
// 收盤後才寫、只有逐筆的日子才有；沒有 → 404，網站改用 1 分 K 復盤。
import { fail, guarded, type Env } from '../../../_lib/db'
import { NO_SECONDS, dayOk, productOk } from '../../../_lib/futures'
import { denied } from '../../../_lib/gate'
import { rawJson, readRows } from '../../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = new URL(request.url).searchParams
  const product = q.get('product')
  const day = q.get('day')
  if (!productOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  if (day === null || !dayOk(day)) return fail(400, `日期格式不對：${day ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await readRows(
      () => db<{ body: string }[]>`
        SELECT payload::text AS body FROM serving.futures_quotes_seconds WHERE product = ${product} AND day = ${day}`,
    )
    if (rows.length === 0) return fail(404, NO_SECONDS)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
