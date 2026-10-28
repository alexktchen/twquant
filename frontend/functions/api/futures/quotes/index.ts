// GET /api/futures/quotes?product=TX|TMF&day=YYYY-MM-DD：大台／微台那一天的行情（每分鐘 K、最新價、昨收；沒給 day＝最新一天）；盤口與大單（flow）另走 /api/futures/flow，這裡不給（T-455，省流量）。
// 只給 admin（T-451／D-220 修訂；富邦行情不得轉傳）。權限一定在讀資料庫之前擋；盤中每 15 秒會變，不進暫存。
import { fail, guarded, type Env } from '../../../_lib/db'
import { QUOTES_NOT_READY, dayOk, productOk } from '../../../_lib/futures'
import { denied } from '../../../_lib/gate'
import { rawJson, readRows } from '../../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = new URL(request.url).searchParams
  const product = q.get('product')
  const day = q.get('day')
  if (!productOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  if (day !== null && !dayOk(day)) return fail(400, `日期格式不對：${day}`)
  return guarded(env, async (db) => {
    const rows = await readRows(() =>
      day === null
        ? db<{ body: string }[]>`
            SELECT (payload - 'flow')::text AS body FROM serving.futures_quotes WHERE product = ${product} ORDER BY day DESC LIMIT 1`
        : db<{ body: string }[]>`
            SELECT (payload - 'flow')::text AS body FROM serving.futures_quotes WHERE product = ${product} AND day = ${day}`,
    )
    if (rows.length === 0) return fail(404, day === null ? QUOTES_NOT_READY : `${day} 沒有這個商品的行情`)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
