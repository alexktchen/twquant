// GET /api/futures/quotes/days?product=TX|TMF：有這個商品行情的交易日（新到舊）與來源、狀態；有模擬單的日子多合計與收盤重算是否一致。只給 admin（T-451）；ms_total＝網友原版的合計（T-456，舊資料沒有 → null）。T-461 起不再回開高走低的合計與算帳戶用的成交。
// T-462：沒有逐筆的日子，併進網友原版的 1 分 K 估算（ms_est＝true），真實資料優先。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { DAYS_LIMIT, productOk, withEstimates, type DayRow, type EstimateRow } from '../../../_lib/futures'
import { denied } from '../../../_lib/gate'
import { readRows } from '../../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const product = new URL(request.url).searchParams.get('product')
  if (!productOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    // T-464：舊日子的 total 含已拿掉的噪音區，日曆的開盤突破只算 ORB_LONG
    const real = await readRows(
      () => db<DayRow[]>`
        SELECT day::text AS day, source, status, COALESCE((payload->'sleeves'->'ORB_LONG'->>'realized')::float8 + COALESCE((payload->'sleeves'->'ORB_LONG'->>'open_pnl')::float8, 0), (payload->>'total')::float8) AS total,
               (payload->>'official_match')::boolean AS match,
               (payload->'ms'->>'total')::float8 AS ms_total FROM serving.futures_quotes
        WHERE product = ${product} ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    const est = await readRows(
      () => db<EstimateRow[]>`SELECT day::text AS day, total FROM serving.futures_ms_estimate WHERE product = ${product} ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    return json({ days: withEstimates(real, est, DAYS_LIMIT) })
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
