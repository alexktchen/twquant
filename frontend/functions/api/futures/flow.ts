// GET /api/futures/flow?product=MTX|TX|TMF&day=YYYY-MM-DD&min=10&live=1：那一天的盤口與大單（T-455／D-220 修訂五）。
// 小台在 serving.futures_paper、大台微台在 serving.futures_quotes，都是 payload 的 flow（fubon_live.py 的 Flow.snapshot）。
// 只給 admin（富邦行情不得轉傳）；權限在讀資料庫之前擋；盤中每 15 秒會變，不進暫存。
// 省流量（2026-10-28 Supabase 流出超量）：大單在這裡就照門檻篩（min），盤中（live=1）不給每分鐘的五檔（books）——那是復盤才要的。
import { fail, guarded, type Env } from '../../_lib/db'
import { BIG_MIN_FLOOR, NO_FLOW, dayOk, flowProductOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = new URL(request.url).searchParams
  const product = q.get('product')
  const day = q.get('day')
  const min = Math.max(BIG_MIN_FLOOR, Math.floor(Number(q.get('min') ?? BIG_MIN_FLOOR)) || BIG_MIN_FLOOR)
  const live = q.get('live') === '1'
  if (!flowProductOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  if (day !== null && !dayOk(day)) return fail(400, `日期格式不對：${day}`)
  return guarded(env, async (db) => {
    // 兩張表欄位一樣（day, payload），小台沒有 product 欄
    const source =
      product === 'MTX'
        ? day === null
          ? db`SELECT day, payload->'flow' AS f FROM serving.futures_paper ORDER BY day DESC LIMIT 1`
          : db`SELECT day, payload->'flow' AS f FROM serving.futures_paper WHERE day = ${day}`
        : day === null
          ? db`SELECT day, payload->'flow' AS f FROM serving.futures_quotes WHERE product = ${product} ORDER BY day DESC LIMIT 1`
          : db`SELECT day, payload->'flow' AS f FROM serving.futures_quotes WHERE product = ${product} AND day = ${day}`
    const rows = await readRows(
      () => db<{ day: string; body: string | null }[]>`
        SELECT day::text AS day,
               CASE WHEN f IS NULL OR f = 'null'::jsonb THEN NULL ELSE jsonb_build_object(
                 'version', f->'version', 'day', day::text, 'product', ${product}::text, 'big_min', ${min}::int,
                 'minutes', f->'minutes', 'buy', f->'buy', 'sell', f->'sell', 'book', f->'book',
                 'big', COALESCE(jsonb_path_query_array(f->'big', '$[*] ? (@[2] >= $m)', jsonb_build_object('m', ${min}::int)), '[]'::jsonb),
                 'books', CASE WHEN ${live}::boolean THEN NULL ELSE f->'books' END
               )::text END AS body
        FROM (${source}) s`,
    )
    if (rows.length === 0) return fail(404, `${day ?? '最新一天'} 沒有這個商品的紀錄`)
    if (rows[0].body === null) return fail(404, NO_FLOW)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
