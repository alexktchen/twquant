// GET /api/futures/days：有小台模擬紀錄的交易日（新到舊），每天的來源、狀態、合計與收盤重算是否一致。只給 admin（T-450／D-220）；ms_total＝網友原版的合計（T-456，舊資料沒有 → null）。T-461 起不再回開高走低的合計與算帳戶用的成交。
// T-462：沒有逐筆模擬單的日子，併進網友原版的 1 分 K 估算（ms_est＝true，source＝minute_estimate），真實資料優先。
// 權限一定在讀資料庫之前擋；盤中那一天的合計會變，不進暫存。
import { guarded, json, type Env } from '../../_lib/db'
import { DAYS_LIMIT, withEstimates, type DayRow, type EstimateRow } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ env }) =>
  guarded(env, async (db) => {
    // T-464：舊日子的 total 含已拿掉的噪音區，日曆的開盤突破只算 ORB_LONG
    const real = await readRows(
      () => db<DayRow[]>`
        SELECT day::text AS day, source, status, COALESCE((payload->'sleeves'->'ORB_LONG'->>'realized')::float8 + COALESCE((payload->'sleeves'->'ORB_LONG'->>'open_pnl')::float8, 0), (payload->>'total')::float8) AS total,
               (payload->>'official_match')::boolean AS match,
               (payload->'ms'->>'total')::float8 AS ms_total
        FROM serving.futures_paper ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    // 估算表還沒建 → 空（readRows 把「表不存在」當成沒有資料），日曆照舊只有真實的日子
    const est = await readRows(
      () => db<EstimateRow[]>`SELECT day::text AS day, total FROM serving.futures_ms_estimate WHERE product = 'MTX' ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    return json({ days: withEstimates(real, est, DAYS_LIMIT) })
  })

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
